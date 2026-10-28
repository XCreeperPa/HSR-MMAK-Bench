# HSR-MMAK-Bench v2 test platform — MIT source release

Original software: XCreeperPa, MIT. Bundled Tabulator retains its own license at service/pages/admin/vendor/tabulator-6.6.1/LICENSE. Game media, question/reference text, and experimental outputs are separately described in the dataset notices.

Python 3.12 was used for validation. Extract the three archives as sibling directories core/, media/, platform/. From platform/: install requirements.txt; run `python prepare_runtime.py`; run `python -m service.app`. Default local address: http://127.0.0.1:8770; task document: /task; admin: /admin. A new local database, server secret and one example agent profile are generated. The admin password is generated at startup and printed only to the local terminal. No live credentials or real participant roster are included. Configure additional participants using the local example roster before starting a new database.

The service retains the original head-only online scoring implementation used in the experiment. The dataset's module-1 body/head revision is a post-test analysis rule; use core/verify.py and reference-answers.json to reproduce revised paper scores. It has not been silently substituted into the historical online service.

Release scope: application Python code, human pages, admin UI, and local example preparation. Production deployment configuration, backups and full client agent harnesses are excluded. See the generated /task for the API protocol. The API asks the agent to fetch media and submit sequentially; it does not run an agent loop itself.
