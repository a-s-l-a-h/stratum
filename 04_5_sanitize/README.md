# Stage 04.5 — JNI Safety Sanitizer (optional)

Optional. Sits between `04_parse` and `05_resolve` (pass 1). Same JSON
shape in and out — this is a pure filter, so it is 100% safe to skip.
Skipping it reproduces today's pipeline exactly (05_resolve reads
04_parse/output/ directly).

## Always on
- Strips `private` methods/fields.
- Strips hiddenapi `blocked` members.
- Never drops a class, never drops a member for a high `sdk_since`.

## Optional
- `--strict` — also drop restricted/unsupported/conditional.
- `--min-sdk N` — also drop members removed at/before API N.

## Usage
    python 04_5_sanitize/main.py \
        --input 04_parse/output \
        --output 04_5_sanitize/output \
        --api-version 35



......................................


python 04_5_sanitize/main.py --input 04_parse/output --output 04_5_sanitize/output --api-version 35 --strict