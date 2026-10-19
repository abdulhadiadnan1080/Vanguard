"""Predict P(a model fails one attempt) for a candidate, and learn from every recorded outcome.

Model: logistic regression with a Gaussian prior on each weight (MAP fit, Newton's method, standard library only).
  * The prior means are the hand-set weights the project already trusts: finder.py WEIGHTS for shape signals,
    weaknesses.json / catalog.json weights for weaknesses and mitigations. With little data the model stays close to
    them; each new labelled task pulls the weights toward what actually happened.
  * One training row per (task, model): y = share of valid trials failed, weighted by the number of valid trials
    (capped at 3 so one heavily re-run task cannot dominate).
  * Features of a task: the signals of the advisory it was built from (full-fix tasks), plus the weaknesses and
    mitigations curated in catalog.json (controlled experiments), plus a per-model offset.

Honest evaluation: leave-one-repo-out (all variants of one repository are held out together), scored with the Brier
score and log loss, against two baselines: the overall fail rate, and the prior weights with no fitting.
"""
import math

import sources

POINT_SCALE = 0.5   # 1 finder point = 0.5 logit; the finder's 6-point shortlist line ~ +3 logit above base
PRIOR_SD = 1.0      # how far one weight may move from its prior per unit of evidence
BIAS_SD = 2.0
WEIGHT_CAP = 3


def task_rows(g):
    """Training rows from the graph: [{task, model, x, y, w, group}]."""
    from ingest import valid_trials
    groups, _ = valid_trials(g)
    rows = []
    for (task, model), ts in sorted(groups.items()):
        node = g.get(task) or {}
        x = for_model(g, task_features(g, task), model.split(":")[1])
        fails = sum(1 for t in ts if t["reward"] < 1.0)
        rows.append({"task": task, "model": model, "x": x, "y": fails / len(ts), "w": min(len(ts), WEIGHT_CAP),
                     "runs": len(ts), "fails": fails, "group": node.get("repo") or task})
    return rows


def task_features(g, task):
    x = {}
    for b in g.edges(src=task, rel="BUILT_FROM"):
        x.update(candidate_features(g, b["dst"]))
    for e in g.edges(src=task, rel="TARGETS"):
        x[e["dst"]] = 1.0
    for e in g.edges(src=task, rel="MITIGATED_BY"):
        x[e["dst"]] = 1.0
    return x


def candidate_features(g, cand):
    return {e["dst"]: 1.0 for e in g.edges(src=cand, rel="HAS_SIGNAL")}


def for_model(g, x, model):
    """Model-specific view of a feature set: a weakness only counts for the models it was recorded on
    (e.g. one weakness was found on Model B, another on Model A), plus a per-model offset."""
    out = {}
    for f, v in x.items():
        if f.startswith("weakness:"):
            models = (g.get(f) or {}).get("models") or []
            if models and model not in models:
                continue
        out[f] = v
    if model != sources.BASE_MODEL:
        out["model:" + model] = 1.0
    return out


def priors(g, rows):
    """{feature: (mean, sd)} from the hand-set weights stored on Signal/Weakness nodes."""
    p = {}
    for n in g.nodes("Signal"):
        if n["id"].startswith("finder:"):
            p[n["id"]] = (POINT_SCALE * (n.get("weight") or 0.0), PRIOR_SD)
        else:  # mitigations are stated in logits already
            p[n["id"]] = (n.get("weight") or 0.0, PRIOR_SD)
    for n in g.nodes("Weakness"):
        p[n["id"]] = (n.get("weight") or 0.0, PRIOR_SD)
    for m in sources.MODEL_IDS[1:]:  # offsets relative to the reference model
        p["model:" + m] = (0.0, PRIOR_SD)
    total_w = sum(r["w"] for r in rows) or 1.0
    base = sum(r["y"] * r["w"] for r in rows) / total_w if rows else 0.3
    p["bias"] = (_logit(min(max(base, 0.05), 0.95)) - 1.0, BIAS_SD)  # signals push up from a lower default
    return p


def fit(rows, prior, iters=30):
    # every feature with a prior is kept, so a signal never seen in training still contributes its prior weight
    feats = sorted({f for r in rows for f in r["x"]} | set(prior) | {"bias"})
    idx = {f: i for i, f in enumerate(feats)}
    mu = [prior.get(f, (0.0, PRIOR_SD))[0] for f in feats]
    prec = [1.0 / prior.get(f, (0.0, PRIOR_SD))[1] ** 2 for f in feats]
    theta = list(mu)
    data = [([(idx["bias"], 1.0)] + [(idx[f], v) for f, v in r["x"].items()], r["y"], r["w"]) for r in rows]
    for _ in range(iters):
        n = len(feats)
        grad = [prec[i] * (theta[i] - mu[i]) for i in range(n)]
        hess = [[0.0] * n for _ in range(n)]
        for i in range(n):
            hess[i][i] = prec[i]
        for xs, y, w in data:
            p = _sigmoid(sum(theta[i] * v for i, v in xs))
            for i, v in xs:
                grad[i] += w * (p - y) * v
                for j, u in xs:
                    hess[i][j] += w * p * (1 - p) * v * u
        step = _solve(hess, grad)
        theta = [t - s for t, s in zip(theta, step)]
        if max(abs(s) for s in step) < 1e-7:
            break
    return dict(zip(feats, theta))


def prior_weights(prior):
    return {f: m for f, (m, _) in prior.items()}


def predict(weights, x):
    return _sigmoid(weights.get("bias", 0.0) + sum(weights.get(f, 0.0) * v for f, v in x.items()))


def explain(weights, x, top=4):
    parts = sorted(((weights.get(f, 0.0) * v, f) for f, v in x.items()), key=lambda t: -abs(t[0]))
    return [(f, round(c, 2)) for c, f in parts[:top] if abs(c) >= 0.05]


def evaluate(rows, prior):
    """Leave-one-repo-out Brier score and log loss for: base rate, prior weights, fitted model."""
    groups = sorted({r["group"] for r in rows})
    out = {name: {"brier": 0.0, "logloss": 0.0} for name in ("base_rate", "prior_only", "fitted")}
    total = 0.0
    for gname in groups:
        train = [r for r in rows if r["group"] != gname]
        test = [r for r in rows if r["group"] == gname]
        if not train:
            continue
        tw = sum(r["w"] for r in train)
        base = sum(r["y"] * r["w"] for r in train) / tw
        p_prior = priors_from(prior, train)
        fitted = fit(train, p_prior)
        for r in test:
            for name, p in (("base_rate", base), ("prior_only", predict(prior_weights(p_prior), r["x"])),
                            ("fitted", predict(fitted, r["x"]))):
                out[name]["brier"] += r["w"] * (p - r["y"]) ** 2
                out[name]["logloss"] += r["w"] * _logloss(p, r["y"])
            total += r["w"]
    for name in out:
        for k in out[name]:
            out[name][k] = round(out[name][k] / total, 4) if total else None
    out["n_rows"], out["n_groups"], out["weight"] = len(rows), len(groups), total
    return out


def priors_from(prior, rows):
    """Re-derive the bias prior from the training rows only (no peeking at the held-out repo)."""
    p = dict(prior)
    tw = sum(r["w"] for r in rows) or 1.0
    base = sum(r["y"] * r["w"] for r in rows) / tw
    p["bias"] = (_logit(min(max(base, 0.05), 0.95)) - 1.0, BIAS_SD)
    return p


# ---------------------------------------------------------------- maths
def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(min(z, 35.0), -35.0)))


def _logit(p):
    return math.log(p / (1 - p))


def _logloss(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _solve(a, b):
    """Gaussian elimination with partial pivoting (the Hessian is small and positive definite)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x
