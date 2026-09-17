# ADR 0001 — No Microsoft Graph MCP server

Date: 2026-09-17
Status: accepted

## Context

Microsoft's [Core MCP getting-started
guide](https://learn.microsoft.com/en-us/rest/api/fabric/articles/mcp-servers/core-remote/get-started-core#optional-add-microsoft-graph-mcp-integration)
suggests adding the Microsoft Graph MCP server so that workspace role operations can be
expressed with email addresses: *"Add Microsoft Graph MCP Server to resolve email addresses
automatically. Without it, you need to provide user principal IDs for role operations."*

Since this framework does not use Core MCP, the question is whether `fab` closes the same
gap, and whether reading access needs Graph.

## What was verified

Against `fab` 1.7.0 and the Fabric REST reference, on 2026-09-17:

**Write side — `fab` has the same gap.**

```
fab acl set <path> [-I <objectId>] [-R <role>]
  -I, --identity   Entra identity objectId
```

It takes an object ID, not an email. Something must resolve `person@company.com` to a GUID.

**Read side — no gap at all.** Fabric enriches role assignments itself.
[List Workspace Role
Assignments](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/list-workspace-role-assignments)
returns:

```json
{
  "principal": {
    "displayName": "Eric Solomon",
    "id": "81fac5e1-2a81-421b-a168-110b1c72fa11",
    "type": "User",
    "userDetails": {"userPrincipalName": "eric@microsoft.com"}
  },
  "role": "Admin"
}
```

Display name *and* UPN come back without Graph. `fab acl ls <ws>.Workspace -l` and
`fab acl get` surface the same object — `fab acl get` even documents
`-q [*].principal`.

**`fab api` cannot reach Graph.** Its `-A/--audience` values are `fabric`, `storage`,
`azure`, `powerbi`. `graph.microsoft.com` is not among them, so Graph is not reachable
through the tool we already have.

## Decision

**Do not add the Microsoft Graph MCP server.**

Reading who has access — the recurring need, and the one raised as important — is fully
served by `fab` today. Graph's only remaining value is resolving an email to an object ID
when *granting* access, and three things argue against buying that with a third MCP server:

1. **It reintroduces the cost the framework just removed.** Every MCP server's tool schemas
   ride in every request. Core MCP was dropped partly for this; adding Graph back for a
   convenience lookup trades away the same thing for less.

2. **Identity resolution is exactly where a human checkpoint belongs.** Granting workspace
   access is a governance act, not a routine one. An agent resolving "sarah" against a live
   directory and acting on whichever Sarah came back is the failure mode, and a live lookup
   is what makes it possible. A reviewed mapping makes the *choice of person* a diff someone
   approved.

3. **A manual lookup leaves a record; a live resolution does not.** The object id and the
   person it belongs to go into the pull request that requests the grant, so the grant is
   reviewable after the fact. Graph resolution happens inside a model turn and leaves
   nothing behind — you would know a grant was made but not on what basis the identity was
   chosen.

## Consequences

**Object ids are looked up by hand at grant time. No mapping file is maintained.**

An earlier draft of this ADR proposed `profiles/identities.yaml` holding UPN to object id.
That file has been removed. A map read only by humans and never by code is a cache with a
staleness risk and no compensating benefit: it has to be kept current, it can disagree with
the directory, and nothing verifies it. Grants are rare enough that the lookup is cheaper
than the upkeep.

So the procedure for granting access is:

```bash
az ad user show --id person@company.com --query id -o tsv     # or the Entra portal
fab acl set ws.Workspace -I <objectId> -R Contributor
```

with the object id and the person it belongs to written into the pull request that requests
the grant. **The PR is the record** — it carries who, which id, which workspace, which role,
and who approved, which is exactly what an access review needs and what a YAML file would
have duplicated less reliably.

Reading access needs nothing at all. Verified live against this tenant on 2026-09-17:

```
GET workspaces/{id}/roleAssignments  ->  200
  Admin   Haotian Qu             haotian.qu@deeeplabs.com
  Admin   Accent Fabric Admin    accentfabricadmin@accentconsult.onmicrosoft.com
```

Display names and UPNs come straight back, so access reviews, drift detection and audit
reporting need no directory lookup and no map.

An object id appearing in a review that nobody recognises is surfaced as-is rather than
resolved. That is the point: an unknown principal holding access is what a review should
shout about, and silently rendering a friendly name for it would hide the finding.

## Alternatives considered

| Option | Why not |
|---|---|
| Add Graph MCP as documented | Per-turn schema cost; removes the human checkpoint from identity resolution; leaves no repo trace |
| Reach Graph via `fab api` | Not possible — Graph is not an available audience |
| Require the Azure CLI as a dependency | Heavier than a standing dependency deserves; `az` is useful for the occasional lookup but nothing else in the framework needs it |
| Maintain `profiles/identities.yaml` | Proposed, then dropped. A map no code reads is a cache with staleness risk and nothing verifying it. Grants are rare; the lookup is cheaper than the upkeep |
| Store object IDs inline in the profile | Mixes identity with topology, and the profile is read on every command — identity has no business being in that path |
