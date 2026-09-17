# PDF resources

This directory contains courseware tracked by Git LFS. Add PDFs below this directory, then run:

```bash
python3 tools/check_resource_pdfs.py
git add resources/pdfs
git commit -m "Add training resources"
git push origin main
```

After the push succeeds, add the repository-relative path, such as
`resources/pdfs/graph/network-flow.pdf`, in the website admin resource form.
