"""Internal member claims and read-only roster snapshots."""
from __future__ import annotations
import hmac
import os
import time
import uuid
from urllib.parse import urlsplit, parse_qs
from cpc_common import connect, uid


class Integration:
    def __init__(self, database):
        self.database = database
        self.path = database.path
        with self.db() as db:
            self.authority = uid(db, 'authority', 'self')

    def db(self):
        db = connect(self.path)
        db.executescript('''
          create table if not exists cpc_claims(
            id text primary key, client text not null, subject text not null,
            person text not null, account_name text not null, note text not null,
            status text not null, reviewer text not null default '', updated integer not null);
          create unique index if not exists cpc_claim_person on cpc_claims(person) where status='approved';
          create unique index if not exists cpc_claim_subject on cpc_claims(client,subject) where status='approved';
          create table if not exists cpc_claim_audit(
            id integer primary key,claim_id text,status text,reviewer text,created integer);
        ''')
        return db

    def roster(self):
        with self.db() as db:
            db.execute('begin immediate')
            members = [{
                'id': uid(db, 'person', row['id']), 'name': row['display_name'] or row['name'],
                'school': row['school'],
            } for row in db.execute('select * from members order by id').fetchall()]
            participations = []
            for honor in self.database._honors_payload(db):
                if not honor['rosterConfirmed']:
                    continue
                participations.append({
                    'id': uid(db, 'participation', honor['id']),
                    'legacy_id': honor['id'], 'event': honor['event'], 'team': honor['team'],
                    'date': honor['date'], 'school': honor['school'], 'official': honor['official'],
                    'members': [uid(db, 'person', m['id']) for m in honor['memberDetails']],
                    'sources': honor['sources'],
                })
            redirects = {uid(db, 'person', r['old_id']): uid(db, 'person', r['member_id'])
                         for r in db.execute('select * from member_redirects').fetchall()}
        return {'schema_version': 1, 'authority_id': self.authority, 'snapshot_complete': True,
                'record_count': len(members) + len(participations) + len(redirects),
                'members': members, 'participations': participations, 'redirects': redirects}

    def submit(self, body):
        for key in ('id', 'client', 'subject', 'person'):
            uuid.UUID(body[key])
        name = str(body.get('account_name', '')).strip()[:100]
        note = str(body.get('note', '')).strip()
        if not name or not note or len(note) > 2000:
            raise ValueError('请填写账号名称及核验说明（最多 2000 字）')
        with self.db() as db:
            db.execute('begin immediate')
            if not db.execute('select 1 from cpc_ids i join members m on cast(m.id as text)=i.local_id where i.kind=? and i.uid=?', ('person', body['person'])).fetchone():
                raise ValueError('成员不存在，请刷新成员列表')
            old = db.execute('select * from cpc_claims where id=?', (body['id'],)).fetchone()
            if old:
                if any(old[k] != body[k] for k in ('client', 'subject', 'person')):
                    raise ValueError('申请 ID 冲突')
                return {'ok': True, 'id': old['id']}
            if db.execute("select 1 from cpc_claims where client=? and subject=? and status in ('pending','approved')", (body['client'], body['subject'])).fetchone():
                raise ValueError('已有待审核或已认证关联，请管理员先处理原关联')
            db.execute('insert into cpc_claims(id,client,subject,person,account_name,note,status,updated) values (?,?,?,?,?,?,?,?)',
                       (body['id'], body['client'], body['subject'], body['person'], name, note, 'pending', int(time.time())))
        return {'ok': True, 'id': body['id']}

    def claims(self, client):
        uuid.UUID(client)
        with self.db() as db:
            rows = [dict(r) for r in db.execute('select id,subject,person,status,updated from cpc_claims where client=?', (client,))]
        return {'schema_version': 1, 'authority_id': self.authority,
                'snapshot_complete': True, 'client_id': client, 'record_count': len(rows), 'claims': rows}

    @staticmethod
    def canonical_person(db, person):
        visited = set()
        while person not in visited:
            visited.add(person)
            old = db.execute("select local_id from cpc_ids where kind='person' and uid=?", (person,)).fetchone()
            redirect = db.execute('select member_id from member_redirects where old_id=?', (old[0],)).fetchone() if old else None
            if not redirect:
                return person
            person = uid(db, 'person', redirect[0])
        raise ValueError('成员重定向存在循环，请先修正档案')

    def review(self, claim, status, reviewer):
        if status not in {'approved', 'rejected', 'revoked'} or not reviewer.strip():
            raise ValueError('状态或审核者无效')
        with self.db() as db:
            db.execute('begin immediate')
            row = db.execute('select * from cpc_claims where id=?', (claim,)).fetchone()
            if not row or (status in {'approved','rejected'} and row['status'] != 'pending') or (status == 'revoked' and row['status'] != 'approved'):
                raise ValueError('申请不存在或状态已改变')
            if status == 'approved':
                person = self.canonical_person(db, row['person'])
                for other in db.execute("select * from cpc_claims where status='approved'").fetchall():
                    if self.canonical_person(db, other['person']) == person or (other['client'],other['subject']) == (row['client'],row['subject']):
                        raise ValueError('该成员或账号已有认证，请先撤销原关联')
                db.execute('update cpc_claims set person=? where id=?', (person,claim))
            stamp = int(time.time())
            db.execute('update cpc_claims set status=?,reviewer=?,updated=? where id=?', (status, reviewer, stamp, claim))
            db.execute('insert into cpc_claim_audit(claim_id,status,reviewer,created) values (?,?,?,?)', (claim,status,reviewer,stamp))


def handle(handler, database, post=False):
    parsed = urlsplit(handler.path)
    if not parsed.path.startswith('/api/integration/v1/'):
        return False
    token = os.environ.get('CPC_SYNC_TOKEN', '')
    if not token or not hmac.compare_digest(handler.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode()):
        handler._send_json({'error': '联动未启用或凭据无效'}, 403)
        return True
    try:
        service = Integration(database)
        if post and parsed.path == '/api/integration/v1/claims':
            result = service.submit(handler._read_json())
        elif not post and parsed.path == '/api/integration/v1/roster/snapshot':
            result = service.roster()
        elif not post and parsed.path == '/api/integration/v1/claims/snapshot':
            result = service.claims(parse_qs(parsed.query).get('client', [''])[0])
        elif not post and parsed.path == '/api/integration/v1/meta':
            result = {'authority_id': service.authority, 'schema_version': 1}
        else:
            handler._send_json({'error': 'not found'}, 404)
            return True
        handler._send_json(result)
    except (ValueError, KeyError, TypeError) as exc:
        handler._send_json({'error': str(exc)}, 400)
    return True
