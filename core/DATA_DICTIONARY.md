# Data dictionary

Primary join key for answer rows: (bank_version, experiment_id, question_id). Public experiment_id is AI01–AI20 or HSR-Player-1–5. Question IDs q0001–q0021 refer only to the bundled bank version; music ordering differs from older bank versions.

Groups: agents=18, traditional=2, players=5. public_label includes the recorded model, effort and host. Unknown model IDs, context policies or adapters remain null or recorded unknown labels; no reconstruction is asserted. Some recorded adapter fields are structured JSON, so JSON files are canonical when CSV cells contain objects.

Answers: answer is the parsed submitted answer object; reasons stores recorded option explanations; annotations stores character_id, x, y and annotation reason when present. Coordinates use original stimulus image pixels; original image dimensions are in questions.json. original_score is the source stored or derived zero score; analysis_score applies the module-1 revision; max_score is the position maximum. The separate annotations field remains authoritative for annotation reasons.

Timing CSV: UTC ISO timestamps answer_received_at (server receipt), answer_committed_at (server commit), delivery_complete_at (recorded material delivery completion). duration_with_delivery_ms includes delivery; duration_post_delivery_ms starts after recorded delivery completion; duration_client_ready_ms starts after the client's materials-ready signal. These scopes and coverage differ. local_request_loop_seconds is a separate local-script wall-clock measure in seconds, not directly interchangeable with server timers. Empty fields are missing. delivery_incomplete records the original flag. Experiment duration_seconds follows duration_definition; service settlement can occur after real activity stopped. Absolute timestamps are record times, not proof of active reasoning.

Original categorical status names are stored unchanged. See validation.json for exact enum values; translation does not merge returned blank answers, missing submissions, generation failures, request timeouts or technical errors. failure_cause_recorded retains archived source labels.

SQLite has questions/reference_answers payload JSON plus experiments, answers, timing and scores tables. The answers/reasons/annotations columns are serialized JSON. CSV is UTF-8 with BOM; nested objects are JSON strings. JSON/JSONL is UTF-8.
