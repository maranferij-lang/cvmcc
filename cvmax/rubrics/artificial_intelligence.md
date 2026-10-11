# Program rubric: Artificial Intelligence

Typical targets: ML engineer intern, data scientist, AI/LLM engineer, research intern, data analyst.

What recruiters look for:
- Strong quantitative training (CS, statistics, applied maths, ML) and Python proficiency (BCG X).
- All software-engineering signals: languages at the top, GitHub, linked projects, stack named.
- A metric and a baseline for every model: "A model with no number attached reads like a class project".
- Deployment and engineering, not only notebook accuracy: serving code, Docker, pipelines, tracking.
- Depth in one framework actually used, not a list of every framework.
- Research outputs: papers, preprints, posters, Kaggle rankings (with rank out of N).
- Relevant coursework (ML, deep learning, probability, linear algebra, optimisation).

Common mistakes: toy benchmark accuracy with no baseline ("98% on MNIST"); no evaluation protocol;
buzzword stacks ("LLMs, GenAI, RAG, agents") with no project behind them; calling an API wrapper
"built an LLM"; certificate collections instead of projects.

Format: Education (GPA, ML coursework, thesis) -> Experience/Research -> Projects (links, metrics) ->
Publications -> Technical Skills grouped (Languages; ML/DL; Data; MLOps/Cloud). One page for interns.

Example bullets (style reference only):
- Fine-tuned a multilingual BERT model on 18K labelled news headlines; macro-F1 0.81 vs 0.64 for a
  TF-IDF + logistic-regression baseline.
- Built a retrieval-augmented Q&A assistant over 2,300 pages of regulations (FAISS); accuracy on a
  150-question test set rose from 52% to 84% vs the model without retrieval.
- Served a gradient-boosted churn model through FastAPI and Docker; scored 250K users nightly.
- Placed in the top 4% (87th of 2,100 teams) in a Kaggle tabular competition.
- Built a 400-ticket labelled test set for an LLM support-ticket classifier; it caught a 9-point accuracy
  drop from a prompt change before release.
- Distilled a 110M-parameter image classifier into a 12M-parameter model; kept 96% of its accuracy and
  cut CPU inference from 180 ms to 25 ms per image.

## Screening order
1. First: degree field and academics; CS, statistics, maths or ML with excellent results, plus Python (BCG X). PhD research internships require a PhD in CS, ML or a related field (Meta research intern posting, 2025).
2. Second: ML systems built and evaluated: framework (PyTorch), experiment setup, analysed results (Meta posting minimum and preferred qualifications).
3. Third: research output and code: publications at venues such as NeurIPS, ICML, ICLR, ACL; open-source contributions (Meta posting, preferred).
- Industry DS/ML roles ask for applied data-science experience and Python (BCG X); list only tools a bullet shows in use (GWU Data Science resume guide).
- First-pass reject: no ML or deep-learning system the candidate built, only courses and tool names; the Meta research-intern posting lists that experience as a minimum qualification.
