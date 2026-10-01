---
title: Fine-tuning and its safeguards
description: What happens when the studio trains a model on your examples, the checks that keep a worse model from ever reaching you, and the results measured on all eleven models.
lead: Training a model on your own examples is called fine-tuning. Done carelessly it makes a model worse in ways nobody notices; the studio's trainer is built so that can't reach you. This page explains what it does, what it checks, and what it achieved on every model.
---

## What training changes

A decision model reads a situation and the allowed answers, and returns how likely each answer is. Fine-tuning continues the model's training on new examples so that its answers move towards yours.

The studio uses **LoRA** (low-rank adaptation): the released model's weights stay exactly as they are, and small extra matrices are trained beside some of its layers. The result is a file of changes of 14 to 270 MB, against hundreds of megabytes to tens of gigabytes for the models themselves. When you use a trained model, the studio loads the original and adds the changes, so it needs no more memory than the original and deleting it is instant.

## How a training run works

:::steps
1. **Split.** Your examples are divided into a part to learn from, a part to check progress on, and a part set aside to test the result (roughly 60/20/20 for small files, 70/15/15 for larger ones). Examples that share a group, or are copies of each other, stay on the same side.
2. **Measure the original.** The released model answers the test examples and a fixed set of general questions first (about a thousand for the small models, 80 to 160 examples for the large ones, which take longer to evaluate). That is the bar to beat.
3. **Learn, while remembering.** Each training step mixes your examples with general examples from two public datasets of decisions. On those, the model is pulled back towards the released model's own answers (a method called distillation), so it keeps what it knew.
4. **Watch for drift.** Every few minutes the studio measures how often the model is right on your progress examples and how far its answers to general questions it never trains on have moved. The step it keeps is the one that learned your task while moving those least, and it stops when practice stops helping.
5. **Calibrate.** One number, a temperature, is fitted so that the new model's percentages match how often it is right: when it says 80%, it should be right about 80% of the time.
6. **Decide.** The checks below run on the examples set aside in step 1.
7. **Prove the saved file.** The saved changes are loaded the way the studio serves models, and must answer exactly like the model that was trained.
:::

## The checks

The new model is added to the studio only if every check passes:

| Check | Passes when |
|---|---|
| **Better on your task** | it is right more often on the test examples, and a resampling test says that is unlikely to be luck (or it is equally right and clearly better calibrated) |
| **Nothing forgotten** | on the general questions, answered before and after, there is no clear drop of 2 points or more, no drop of 3 points at all, and no clear drop of 1 point or more that the gain on your task doesn't outweigh three times over |
| **No collapse** | it doesn't give the same answer to almost every example when the right answers vary |
| **The saved file is the trained model** | loaded the way the studio serves it, its answers match the trained model's to within 0.02 (in practice they match exactly) |

If only the forgetting check fails, the studio starts again once from the original model, with half the learning rate and twice the pull towards the original answers. If that also fails, the original stays, and the result page says why.

:::note Forgetting
A model trained only on a narrow task can lose ground on everything else; this is called catastrophic forgetting. The general questions are never trained on, so a drop there is real. A small drop alongside a large gain on your task is kept and shown on the result page; the original model is still there for anything else.
:::

## Every model, measured

Each of the eleven models was trained by the studio's own trainer on an NVIDIA GB10 that other programs were using at the same time, with the settings the studio uses by default. **Before → after** is accuracy on examples set aside before training; **General** is the change on the general questions, in points. Peak memory is the most GPU memory the training held ("about": observed while running rather than recorded by the run).

| Model | Task | Before → after | General | Time | Peak memory |
|---|---|---|---|---|---|
| Julia 1 | support tickets (the example file) | 52% → 91% | −0.4 | 6 min | 7 GB |
| Julia 1 | policy topics | 51% → 68% | −1.5 | 13 min | about 8 GB |
| Julia 1 | emotions in conversations | 33% → 50% | −1.5 | 16 min | about 8 GB |
| Laya | policy topics | 59% → 80% | +0.2 | 29 min | about 4 GB |
| Laya | business workflows | 36% → 63% | +12.1 | 76 min | 19 GB |
| Laya Multilingual | business workflows | 34% → 62% | +12.7 | 37 min | about 4 GB |
| Laya Typed-Decisions | policy topics | 61% → 81% | +0.2 | 21 min | 4 GB |
| GLiNER2.5 Decide | policy topics | 66% → 75% | +0.7 | 82 min | 24 GB |
| Kev 0.5B | policy topics | 65% → 79% | +0.6 | 39 min | 3 GB |
| Kev 4B | policy topics | 77% → 82% | +0.2 | 65 min | 27 GB |
| Intern-Decision 4B | emotions in conversations | 61% → 76% | +0.2 | 74 min | 12 GB |
| Lev | policy topics | 75% → 82% | +2.5 | 72 min | 14 GB |
| CLM 8B | business workflows | 39% → 68% | +11.2 | 65 min | 16 GB |
| Jev-Omni | business workflows (160 records) | 62% → 77% | 0.0 | 161 min | 24 GB |

The tasks:

- **Policy topics**: 1,034 sentences from US State of the Union addresses, each labelled with one of 16 policy areas from the Comparative Agendas Project.
- **Emotions in conversations**: 1,088 short personal stories from EmpatheticDialogues, each labelled with one of 16 emotions.
- **Business workflows**: 1,280 cases from the typed-decisions dataset (customer service, invoices, security incidents and agent traces), each with several choice, yes/no and scale questions about a JSON record.
- **Support tickets**: the Train page's built-in example file of 420 IT support tickets.

Every one of these passed the saved-file check with a largest difference of 0 to 0.00005. The checks also refused when they should: CLM 8B on policy topics went from 28% to 76% but lost 2.5 points on general questions, so it wasn't kept; and Julia 1 on business workflows, which it already got 91% right, was left unchanged with that explanation.

Repeating a run with a different split moves these numbers by a few points; Julia 1 on policy topics, for example, gained between 15 and 19 points across six runs. Every run, with its settings, is listed in the repository's `docs/trainer/RESULTS.md`.

:::note Data terms
A model trained on a dataset carries that dataset's terms. EmpatheticDialogues, behind the two emotion models above, allows non-commercial use only; check your own data's terms the same way before sharing a model trained on it.
:::

## Hardware

| Computer | Training |
|---|---|
| NVIDIA RTX 30 series or newer, data-centre GPUs, DGX Spark and other GB10 machines | on |
| Apple M2 or newer, macOS 14 or newer | on |
| Apple M1, or a Mac with less than 12 GB | experimental: slower, full precision |
| NVIDIA RTX 20 series and T4 | experimental |
| AMD GPUs on Linux | experimental |
| Intel Arc, Core Ultra 200V and 200H and newer | experimental |
| processor only, older GPUs, Core Ultra Series 1 graphics, NPUs | off |

Before each training, a short self-test compares the GPU's arithmetic with the processor's, so a broken driver can't produce a broken model. One training runs at a time on a computer, within a memory limit set from what its model needs, and at a lower priority than your other programs. If memory runs short because of other programs, training waits and continues by itself. Only the NVIDIA GB10 has been measured so far; the other rows follow the published behaviour of each platform's PyTorch support.
