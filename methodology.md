# Pairwise Experimental Design for Evaluating Symbolic Music Models

## 1. Overview

We evaluate 12 generative models for symbolic music using **pairwise comparisons only**.

Each trial presents:

* Two audio clips (A, B)
* Same prompt
* Binary preference for each dimension:

  * Fidelity
  * Correctness
  * Diversity
  * Overall Quality

---

## 2. Number of Models and Pairs

Total models:

[
N = 12
]

Number of unique model pairs:

[
N_{pairs} = \binom{N}{2} = \frac{12 \cdot 11}{2} = 66
]

---

## 3. Target Statistical Power

We aim to reliably estimate model rankings using a Bradley–Terry model.

Empirical guideline:

* Each model should appear in **at least 40 comparisons**

Let:

[
C = \text{comparisons per model} = 40
]

Total comparisons required:

[
N_{comparisons} = \frac{N \cdot C}{2} = \frac{12 \cdot 40}{2} = 240
]

(division by 2 because each comparison involves two models)

---

## 4. Participant Load

Let:

[
T = \text{comparisons per participant}
]

Number of participants required:

[
P = \frac{N_{comparisons}}{T}
]

---

## 5. Exact Configurations

### Option A (recommended)

[
T = 30
]

[
P = \frac{240}{30} = 8
]

---

### Option B (lower fatigue)

[
T = 20
]

[
P = \frac{240}{20} = 12
]

---

### Option C (high efficiency)

[
T = 40
]

[
P = \frac{240}{40} = 6
]

---

## 6. Recommended Setup

* Participants: **8–12**
* Comparisons per participant: **20–30**
* Total comparisons: **240**
* Comparisons per model: **40**

---

## 7. Sampling Strategy

### 7.1 Balanced Pair Sampling

We must ensure:

1. Each model appears ≈ same number of times
2. Each pair is sampled multiple times (ideally 3–5)

Let:

[
R_{pair} = \text{repetitions per pair}
]

If we want uniform coverage:

[
R_{pair} = \frac{N_{comparisons}}{N_{pairs}} = \frac{240}{66} \approx 3.64
]

→ In practice:

* Some pairs shown 3 times
* Some pairs shown 4 times

---

### 7.2 Sampling Algorithm

For each comparison:

1. Sample a model pair (balanced frequency)
2. Randomly sample one of the 10 outputs for each model
3. Randomize left/right position

---

## 8. Data Structure

Each row:

| rater | model_A | model_B | fidelity_A | correctness_A | diversity_A | quality_A |
| ----- | ------- | ------- | ---------- | ------------- | ----------- | --------- |

Binary values (1 if A preferred, 0 otherwise)

---

## 9. Statistical Model

Use Bradley–Terry model (per dimension):

[
P(A > B) = \frac{e^{\beta_A}}{e^{\beta_A} + e^{\beta_B}}
]

With optional random effects:

[
logit(P(A > B)) = \beta_A - \beta_B + (1 | rater)
]

---

## 10. Power Analysis (Interpretation)

With:

* 40 comparisons per model
* ~3–4 observations per pair

Expected performance:

* Reliable ranking of models
* Good separation for medium effects (d ≈ 0.5)
* Robust to rater noise

---

## 11. Critical Design Notes

### 11.1 Avoid Sample-Level Bias

* Always sample outputs randomly
* Never fix specific clip pairs

### 11.2 Randomization

* Shuffle pair order
* Randomize A/B position

### 11.3 Fatigue Control

* Max 30 comparisons per participant
* Estimated duration: 20–30 minutes

---

## 12. Summary Table

| Quantity                    | Value |
| --------------------------- | ----- |
| Models                      | 12    |
| Unique pairs                | 66    |
| Total comparisons           | 240   |
| Comparisons per model       | 40    |
| Comparisons per participant | 20–30 |
| Participants                | 8–12  |

---

## 13. Key Advantages

* Lower participant count
* Higher decision consistency
* Strong ranking capability

---

## 14. Limitations

* No absolute scores
* More complex statistical analysis
* Requires careful balancing

---

## 15. Conclusion

A fully pairwise design is efficient and statistically robust if:

* comparisons are balanced
* each model is sufficiently sampled (≥40 comparisons)
* participant workload is controlled

This configuration minimizes human effort while preserving reliable model ranking.
