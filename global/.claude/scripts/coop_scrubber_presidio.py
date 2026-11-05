#!/usr/bin/env python3
"""Presidio worker for coop_scrubber.py. Runs inside the private venv (see coop_scrubber_install_presidio.sh), never
in the system Python.

stdin:  {"texts": ["...", ...]}
stdout: {"spans": [[[start, end, "PERSON"|"EMAIL"|"PHONE"], ...], ...]}   one list per text, same order
Nothing else is printed; a failure exits non-zero and the caller carries on without Presidio.
"""
import json
import sys

KINDS = {"PERSON": "PERSON", "EMAIL_ADDRESS": "EMAIL", "PHONE_NUMBER": "PHONE"}
MIN_SCORE = {"PERSON": 0.6, "EMAIL_ADDRESS": 0.5, "PHONE_NUMBER": 0.4}


def main():
    texts = json.load(sys.stdin)["texts"]
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    provider = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
    })
    engine = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
    out = []
    for text in texts:
        found = engine.analyze(text=text, language="en", entities=list(KINDS))
        out.append([[r.start, r.end, KINDS[r.entity_type]] for r in found if r.score >= MIN_SCORE[r.entity_type]])
    json.dump({"spans": out}, sys.stdout)


if __name__ == "__main__":
    main()
