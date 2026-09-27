# Prompt versions

Every agent or generation prompt is recorded here as versioned YAML. Edit the
next version here first, then copy it into the runtime that requires another
format. `apps/api/tests/test_prompt_versions.py` checks that the Pipelex
`.mthds` files and historical image-generation prompts match their YAML
records. Production CD synchronizes the published `voice-intake/` YAML to the
Vapi assistant and verifies the remote result before deploying the API.

| Directory | Contents |
| --- | --- |
| `voice-intake/` | Vapi first message and system prompt, versioned together. |
| `claims-analysis/` | Pipelex system and task prompts for each analysis method version. |
| `fixture-media/` | Historical prompts used to produce synthetic case images. |

These YAML files contain instructions to software agents or image generators;
they are version records, not instructions for contributors reading this repo.
