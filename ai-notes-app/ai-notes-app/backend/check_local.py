"""Local AI provider self-check for AI Notes (Day 37).

Unlike check_groq.py, this makes no network call and needs no API key —
it just runs every local_ai.py feature against a sample note and prints
the results, so you can see at a glance what AI_PROVIDER=local actually
produces before pointing the real app at it.
"""
import json

from local_ai import (
    local_concepts,
    local_digest,
    local_entities,
    local_flowchart,
    local_polish,
    local_study,
    local_summarize,
    local_tags,
    local_tasks,
    local_title,
)

SAMPLE_NOTE = (
    "Standup notes: met with Sarah Chen about the Q3 budget. "
    "We need to email the finance team the updated numbers by Friday. "
    "Also remember to book the conference room for next Tuesday's review. "
    "Machine Learning uses Python and Scikit-learn for model development. "
    "A neural network is a computing system inspired by biological brains."
)

print("AI Notes - Local provider check")
print("--------------------------------")
print("No API key needed. No network call made. Everything below ran locally.\n")

print("Sample note:")
print(" ", SAMPLE_NOTE, "\n")

print("Summary:", local_summarize(SAMPLE_NOTE))
print("Title:", local_title(SAMPLE_NOTE))
print("Polished:", local_polish("this note   has messy  spacing ,and no caps"))
print("Tags:", local_tags(SAMPLE_NOTE))
print("Entities:", json.dumps(local_entities(SAMPLE_NOTE)))
print("Concepts:", local_concepts(SAMPLE_NOTE))
print("Tasks:", json.dumps(local_tasks(SAMPLE_NOTE)))
print("Digest:", local_digest("Task due soon: Email finance team\nTasks piling up: 2 open tasks"))

flowchart = local_flowchart(SAMPLE_NOTE)
print(f"Flowchart: {len(flowchart['nodes'])} nodes, {len(flowchart['edges'])} edges")

study = local_study(SAMPLE_NOTE)
print(
    f"Study pack: {len(study['keyPoints'])} key points, {len(study['qna'])} Q&A, "
    f"{len(study['mcqs'])} MCQs, {len(study['flashcards'])} flashcards, "
    f"{len(study['definitions'])} definitions"
)

print("\nLocal provider looks good. Set AI_PROVIDER=local in backend/.env to use it in the app.")
