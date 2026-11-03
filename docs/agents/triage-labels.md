# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the "Label in our tracker" column to match whatever vocabulary you actually use.

## Presence on the tracker

`/triage` applies these strings; it does not invent new names. `wontfix` is already a default label on `gonglidanmm/vultr-proxy-switcher`. `needs-triage`, `needs-info`, `ready-for-agent`, and `ready-for-human` are not default GitHub labels. Create any that `gh label list` does not show before the first triage:

```bash
gh label create "needs-triage" --description "Maintainer needs to evaluate this issue" --color "FBCA04"
gh label create "needs-info" --description "Waiting on reporter for more information" --color "D876E3"
gh label create "ready-for-agent" --description "Fully specified, ready for an AFK agent" --color "0E8A16"
gh label create "ready-for-human" --description "Requires human implementation" --color "1D76DB"
```

Category roles `bug` and `enhancement` are default GitHub labels on this repo. They are not part of the table above. Do not retarget `wontfix`: the existing label string already matches.
