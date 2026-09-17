# Access control — five layers, three different mechanisms

Fabric does not have one access model. It has five, they are configured by different means,
and — the part that catches people — **they do not compose the way you would expect.**

| # | Layer | Granularity | Configured by | In the repo? |
|---|---|---|---|---|
| 1 | Workspace roles | whole workspace | `fab acl set` / REST | no — grants are PRs |
| 2 | Item permissions | one lakehouse, warehouse, report | REST | no |
| 3 | **OneLake data access roles** | folder inside a lakehouse | REST (`dataAccessRoles`) | **yes** — role definitions as JSON |
| 4 | **SQL endpoint: GRANT/DENY, RLS, CLS** | table, row, column | **T-SQL only** | **yes** — `.sql` under `fabric/` |
| 5 | Semantic model RLS | model roles / DAX filters | TMDL | yes — synced by git integration |

Verified live against this tenant on 2026-09-17: layers 1 and 3 both answer through
`fab api`.

```
GET workspaces/{ws}/roleAssignments                  -> 200
      Admin  Haotian Qu          haotian.qu@deeeplabs.com
      Admin  Accent Fabric Admin accentfabricadmin@accentconsult.onmicrosoft.com

GET workspaces/{ws}/items/{lakehouse}/dataAccessRoles -> 200
      role: DefaultReader  (1 member)
```

---

## The trap: layer 3 does not govern layer 4

> OneLake data access roles restrict what a principal sees **through OneLake** — the lake
> view, notebooks, OneLake APIs. They have **no effect on the SQL analytics endpoint.**

So restricting a folder and believing the data is protected is wrong. The same rows come
back through SQL. Microsoft states this directly in the [OneLake security
model](https://learn.microsoft.com/en-us/fabric/onelake/security/data-access-control-model),
and it is the single most important thing to know here, because the failure is silent: the
OneLake restriction *works*, it is just not the only door.

**Anything genuinely sensitive needs layers 3 and 4 configured together, and something that
checks they still agree.** They are written in different languages, stored in different
places, and nothing in Fabric reconciles them.

That reconciliation is a job for `data-reconcile` (a `verify access` mode), not for a human
remembering. It is not built yet — see the register.

---

## What tooling exists, by layer

**Layers 1–3 — API, so `fabctl` can own them.**

```bash
fab acl ls ws.Workspace -l                                   # who has workspace access
fab api workspaces/{ws}/items/{lh}/dataAccessRoles           # folder roles
fab api workspaces/{ws}/items/{lh}/dataAccessRoles -X put -i roles.json
```

Because layer 3 is JSON over an API, role definitions belong in the repo and are diffable.
That makes "who can read the PII folder" a reviewable change rather than a portal click
nobody sees. Note it is **public preview** — treat the payload shape as unstable and pin
what you observe.

**Layer 4 — no API exists. T-SQL only.**

Row- and column-level security are `CREATE SECURITY POLICY`, `GRANT`, `DENY` and masking
statements executed against the SQL analytics endpoint. There is no REST surface, no `fab`
command, and no MCP tool. The consequence for this framework is that access policy becomes
*code*:

```
fabric/
  lh_silver_retail_curated.Lakehouse/
    security/
      010_roles.sql          -- CREATE ROLE, GRANT / DENY per table
      020_rls_customers.sql  -- predicate function + SECURITY POLICY
      030_cls_masking.sql    -- column masking
```

Idempotent, ordered, reviewed in PRs like any other code, and applied by executing them
against the endpoint. Which makes them a **DML-class action** under the verb rules: they
change state without changing an item definition, so they must be audited and they must not
be run casually. They are also the strongest argument for the DML agent's plan/approve/apply
discipline — a `DENY` with a wrong predicate silently over- or under-exposes data, and
neither direction announces itself.

**Layer 5 — TMDL in git**, synced by the normal git integration. Nothing special needed.

---

## Practical guidance

- **Grant to groups, not people.** Layer 1 and 3 both accept groups. A group turns every
  future joiner and leaver into a directory change rather than a Fabric change, and keeps
  the repo out of the business of tracking individuals.
- **Never grant an agent identity more than Contributor.** Contributor cannot delete items.
  An agent that cannot delete cannot destroy a lakehouse through a bad loop.
- **Treat layer 4 files as the sensitive ones.** A change to `security/*.sql` deserves a
  narrower reviewer set than a change to a transformation notebook — it is the only layer
  where a subtle edit changes who can see what without changing anything visible.
- **Do not use OneLake folder roles as the only control on anything regulated.** See the
  trap above.

---

## Open

- **Layer 3 write payload is unverified.** Reading works; the `PUT` body shape for creating
  or updating a data access role has not been exercised. Public preview, so it may change.
- **Layer 4 execution path is unbuilt.** Running `.sql` against the SQL endpoint needs either
  a SQL connection (`pyodbc` / `sqlcmd`) or a notebook, and neither is wired into `fabctl`
  yet. This is the largest unbuilt piece of the access story.
- **No reconciliation between layers 3 and 4.** Nothing currently detects the two disagreeing,
  which is the failure this document exists to warn about.
