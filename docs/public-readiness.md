# Public Readiness Notes

This repository is intended to keep deployment-specific and personal data outside the public source tree.

Before making an existing private repository public:

- Scan the current tree for secrets, personal names, addresses, document paths, and deployment-specific hostnames.
- Keep real `.env` files, mounted document data, generated normalized artifacts, model caches, and local memory files out of git.
- Store personal deployment values in a separate private repository or environment-specific config.
- Review reusable automation dependencies. Public examples should avoid hardcoded personal package owners.
- Audit git history separately. Removing personal examples from the current tree does not remove them from previous commits.

If old commits contain private information, prefer publishing from a fresh clean import or rewriting history before flipping repository visibility to public.
