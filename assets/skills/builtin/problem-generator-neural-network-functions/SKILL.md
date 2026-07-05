---
name: problem-generator-neural-network-functions
description: Generate novel, mathematically meaningful research problems about neural-network functions (e.g., softmax, sigmoid, tanh networks) by exploiting structural analogies with polynomials, guided by intrinsic mathematical aesthetics such as simplicity, symmetry, and duality.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge read_runtime_file
metadata:
  title: Problem Generator for Neural-Network Functions
  category: builtin
  tags: research,question-design,neural-networks,functions
  skill-standard: agentskills.io/v1
---

# Problem Generator for Neural-Network Functions

## Usage Hint
- Use this skill to generate research problems about neural-network functions.
- Use it when the broad topic is neural-network-function theory but the concrete theorem target is not yet fixed.

## Summary
- Generate novel, mathematically meaningful problems about neural-network functions by exploiting structural analogies with polynomials.
- Prefer questions that are structurally clean, aesthetically rich, and precise enough to become real research targets.

## Analogy Background
- Analogy is a powerful mathematical thinking tool for exploring unknown structures from known ones.
- In this skill, analogy does not mean superficial similarity. It means building a structural mapping between two different objects, identifying which components correspond and how their interactions match.
- The analogy between polynomials and neural-network functions is especially strong because both are organized around linear-combination approximation.
- Useful correspondences include:

| Polynomials | Shallow Neural Networks (One Hidden Layer) |
| --- | --- |
| Monomial $x^k$ | Neuron output $\sigma(w_k x + b_k)$ |
| Basis set $\{x^k\}_{k=0}^n$ | Activation family $\{\sigma(w_k x + b_k)\}_{k=1}^N$ |
| Polynomial $P(x) = \sum_{k=0}^n a_k x^k$ | Network output $f(x) = \sum_{k=1}^N v_k \sigma(w_k x + b_k)$ |
| Coefficient $a_k$ | Output-layer weight $v_k$ |
| Degree $n$ | Number of neurons $N$ |
| Weierstrass approximation by polynomials | Universal approximation by one-hidden-layer networks |

## Execution Steps
1. Read the available local background material on known theorems or problems about polynomials before generating new neural-network-function questions.
2. Restate the topic and identify the polynomial prototypes or theorem patterns that may transfer well to neural-network functions.
3. Use analogy as structural mapping, not superficial similarity: identify which polynomial objects, parameters, approximation roles, extremal properties, or uniqueness mechanisms have plausible neural-network counterparts.
4. For each polynomial prototype, abstract its core mathematical property, such as a parameter bound, extremal characterization, uniqueness phenomenon, algebraic closure property, or approximation principle.
5. Replace the key polynomial complexity parameter, such as degree or coefficient structure, with a neural-network complexity measure, such as neuron count, width, depth, or output-layer weights.
6. Formulate a direct analogue on neural-network functions and refine it using mathematical aesthetics such as simplicity, symmetry, duality, and conceptual cleanliness.
7. Test the candidate on special cases and screen out trivial, ill-posed, or aesthetically weak formulations; if needed, adjust the problem and try again.
8. Present and number the strongest generated questions, making the analogy and the novel twist explicit.
9. After multiple failed attempts, if the generated problem still lacks mathematical logic or mathematical aesthetics, abandon it and state the failure reason rather than forcing a weak question.

## Tool Calls
- `read_runtime_file`: Read local files containing known theorems, prototype problems, or notes on polynomials before generating analogues.
- `query_memory`: Avoid regenerating prior failed or already-selected neural-network-function questions.
- `search_knowledge`: Reuse known theorem summaries, structural analogies, and prior conclusions.

## File References
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/references/`
- `projects/<project_slug>/references/surveys/`

## Output Contract
- Return one or more numbered candidate problems.
- For each strong candidate, give a precise problem statement and explicitly highlight the analogy and the novel twist.
- When a candidate should survive long iterations, present it clearly enough for later retrieval.
- When one candidate becomes the actual working target, state the selected problem explicitly rather than leaving the active problem implicit.
- If repeated attempts fail, state the failed-generation reason explicitly instead of pretending success.

## Notes
- Example analogue: polynomials form a ring under addition and multiplication; ask whether the relevant class of neural-network functions forms an analogous algebraic structure.
- If repeated attempts still produce mathematically weak or trivial questions, state the failure reason explicitly instead of forcing a bad problem.
- Prefer this skill over the generic problem-generator skill when the research topic is specifically about neural-network functions such as softmax, sigmoid, or tanh networks.
