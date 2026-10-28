# HSR-MMAK-Bench v2 — Core dataset 1.0.0

Author: XCreeperPa. Released 2026-10-29. Related preprint: https://doi.org/10.5281/zenodo.23735304

21 questions, 39 media assets, 18 agent configurations, 2 traditional fixed QA scripts, 5 active players, and all 525 question positions. Human public IDs match the paper. Traditional tests are not counted as agents. Test bank: 2-four-modules-20261025-stage2-music-shuffle.

## Files and use

questions.json: complete prompts, option ordering, stimuli IDs, module and maximum scores. reference-answers.json: answer keys, recorded explanations/sources, original head polygons and revised body rectangles. characters.json: character roster used by the bank. assets.json: deployed media mapping/hash and available source metadata. experiments.json/csv: public configuration and execution fields. answers.jsonl/csv: all question-position answers, reasons, annotation coordinates, states and scores. timing.csv: recorded timing fields. scores.csv: 25 rows of original and revised totals/module scores. results.sqlite3: queryable copy of the same public content; nested JSON fields are strings. verify.py: independent numerical checks. scoring/: original numerical scoring implementation (MIT).

Extract core-data.zip into core/, media.zip into media/, and optional platform-code.zip into platform/, as siblings. Run `python core/verify.py`. Only standard-library Python is needed. Raw media are found under media/media/; paths in assets.json are relative to the media archive root.

## Scores and missingness

Current analysis score: module 1 name plus matching head = 1; name plus matching body but outside head = 0.5; otherwise 0. Per target take the highest eligible annotation; boundaries included. Body rectangles were manually adjusted after testing. Original head-only scores are retained separately. Other modules use original option scoring. Original answers, reasons and coordinates are preserved; no new human reasons were generated.

All 525 positions remain in score denominators. A null answer means no recorded submission, not a fabricated blank. Source status explicit_abstention is retained for traceability and displayed in Chinese as 返回了空回答; it does not establish intention. not_submitted = 未提交; output_failure = 输出失败; request_timeout = 请求超时; technical_failure = 技术失败. See validation.json for actual enum counts. Failure causes are recorded labels, not independently established causal diagnoses. Human reasons were not collected; some AI annotation reasons are missing. Empty reason objects and null values retain their recorded distinction.

Each experiment is one configuration/run. Sampling, host capabilities and service failures differ; this release does not add repeated runs. Experiment field evaluation_mode_recorded retains source online/local enum; research grouping uses agents/traditional/players. Use group for the distinction, not deployment locality. Kimi work corrects an application-label mistake; scores and answers are unaffected.

## Privacy and versioning

This is a new allowlisted export, not the private SQLite archive. No participant real-name mapping, questionnaires, administrator notes, source trial IDs, raw provider streams, credentials, server backups or complete event/transfer logs are included. Full timing definition is in DATA_DICTIONARY.md. Null remains missing. Future corrections receive a new version with a change log. Original sealed sources were opened read-only.

## Licensing

See LICENSE.md and THIRD_PARTY_NOTICES.md. Original writing/analysis: CC BY 4.0; software: MIT; game media excluded. Full platform source is supplied as a separate archive in this release. No GitHub repository URL is asserted.
