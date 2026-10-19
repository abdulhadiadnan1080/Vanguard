# Weakness specs

One JSON object per weakness in `weaknesses.json`. A weakness is a property of a task that makes a model fail. It starts as a **hypothesis**, becomes **supported** once controlled experiments show it, and is **proven** once a holdout of real tasks confirms it.

The miner reads this file and scores candidates with every weakness whose `status` is not `rejected`, weighted by `weight`.

## Fields
| Field | Meaning |
|---|---|
| `id` | short slug, e.g. `unreported-sibling-sites` |
| `models` | which models it affects: `["MODEL_A"]`, `["MODEL_B"]` or both |
| `description` | one or two sentences in plain language |
| `trigger` | the exact conditions, as precise as the evidence allows |
| `status` | `hypothesis`, `supported`, `proven` or `rejected` |
| `evidence` | list of `{source, runs, failures, note}`: experiments, mined tasks, earlier experiment tracks |
| `weight` | miner points added when a candidate matches (set from the evidence; keep 0 for untested ideas) |
| `detect` | how to spot it in a real fix (all given conditions must hold): `min_repeated_fix_sites`, `min_src_files`, `max_src_files`, `min_src_dirs`, `min_functions`, `min_hunks`, `max_hunks`, `summary_regex`, `cwes`, `languages`, `min_advisories_same_repo` |
| `owner`, `updated` | who last changed it, and when |

Update a weakness by editing its entry. The experiments log in the knowledge base holds the raw runs behind the `evidence` numbers.
