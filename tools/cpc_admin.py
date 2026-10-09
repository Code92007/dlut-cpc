#!/usr/bin/env python3
"""Review internal claims against the live local database; no seed import."""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import Database
from cpc_integration import Integration

parser = argparse.ArgumentParser()
parser.add_argument('--database', default=os.environ.get('DATABASE_PATH', 'runtime/dlut_cpc.sqlite3'))
sub = parser.add_subparsers(dest='command', required=True)
sub.add_parser('list')
sub.add_parser('meta')
review = sub.add_parser('review')
review.add_argument('claim')
review.add_argument('status', choices=['approved', 'rejected', 'revoked'])
review.add_argument('--reviewer', required=True)
args = parser.parse_args()
if not Path(args.database).is_file():
    parser.error('数据库不存在；不会创建或导入初始快照')
service = Integration(Database(args.database))
if args.command == 'review':
    service.review(args.claim, args.status, args.reviewer)
    print('已更新审核状态')
elif args.command == 'meta':
    print(service.authority)
else:
    with service.db() as db:
        for row in db.execute('select c.*,m.name,m.school from cpc_claims c left join cpc_ids i on i.uid=c.person left join members m on cast(m.id as text)=i.local_id order by c.updated desc'):
            print(json.dumps(dict(row), ensure_ascii=False))
