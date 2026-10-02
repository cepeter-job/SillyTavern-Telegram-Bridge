# Character-Reference /imagine Routing Design

**Date:** 2026-10-02  
**Status:** Design approved; implementation not started

## Goal

Extend the existing `/imagine` workflow so current-scene generation can reuse the active native SillyTavern character PNG as a visual identity reference when the selected image model supports image editing/reference input, while preserving the existing text-only generation path and image-only Telegram delivery.

The feature must keep SillyTavern as the source of truth for character cards and must not create a second persistent portrait store.

## User intent and success criteria

The active character portrait already exists in the SillyTavern character PNG. When visualizing a roleplay scene, the bridge should use that portrait to improve recurring-character consistency instead of relying only on the text description.

Success means:

- `/imagine -> Current Scene` automatically uses the active character PNG when the active route is reference-capable.
- Auto mode prefers the configured reference-capable model when a valid active character PNG exists.
- Auto mode falls back to the configured text model when no usable character image exists.
- Manual model selection remains authoritative and is never silently replaced by Auto routing.
- Character switching immediately changes the image reference source.
- The bridge does not copy character portraits into persistent bridge-owned storage.
- Telegram receives only the final generated image; the temporary progress message is removed after delivery as in the current behavior.
- Existing text-only image providers and existing private provider catalogs remain compatible.

## Existing architecture

The current image flow is owned by `bridge/image_generation.py`.

Today it:

1. reads per-session image model and size metadata;
2. resolves an enabled image provider/model from the private provider catalog;
3. builds a bounded current-scene prompt from committed story state through the Utility route;
4. sends an OpenAI-style `/images/generations` JSON request;
5. accepts base64 or HTTPS image results;
6. sends the final image to Telegram and removes the temporary progress message.

The bridge already resolves active character cards through the safe native SillyTavern character-card path helpers in `bridge.card_content` / `bridge.cards`. Those helpers remain the authority for locating the active card.

## Design overview

Add a capability-aware image routing layer to the existing image-generation subsystem rather than creating a second image subsystem.

Conceptually:

```text
/imagine -> Current Scene
            |
            +-- active character PNG usable?
            |       |
            |       +-- yes + Auto -> preferred reference-capable model
            |       |                  + character PNG reference
            |       |
            |       +-- yes + manual reference model -> selected model + reference
            |       |
            |       +-- yes + manual text model -> selected model, text only
            |
            +-- no + Auto -> preferred text model
            |
            +-- no + manual model -> selected model
```

The approved default routing policy is:

- Auto reference route: `step-image-edit-2`
- Auto text route: `chroma`

The routing implementation must make those defaults configurable rather than embedding provider-specific conditionals throughout `/imagine`.

## Provider capability metadata

Keep the existing `image_models: [string, ...]` format unchanged for backward compatibility.

Add optional provider-level capability metadata keyed by exact model ID:

```yaml
image_enabled: true
image_models:
  - chroma
  - step-image-edit-2

image_model_capabilities:
  chroma:
    mode: text
  step-image-edit-2:
    mode: reference
    edit_route: openai

image_auto:
  text_model: chroma
  reference_model: step-image-edit-2
```

### Capability values

`mode`:

- `text`: text-to-image only.
- `reference`: requires or accepts one image reference through the image-edit route.
- `both`: supports text generation and reference/edit generation.

`edit_route`:

- `openai`: use the OpenAI-compatible image edit transport.
- Future transports may be added later, but this feature must not add unused abstractions.

Unknown or absent capability metadata means `text`, preserving current behavior.

### Backward compatibility

Existing catalogs that only declare:

```yaml
image_models:
  - z-image-turbo
```

continue to work exactly as they do now.

No existing provider config is required to add capability metadata unless reference routing is desired.

## Session selection and Auto mode

The current session metadata stores an exact provider/model selection. Extend it with a stable Auto sentinel rather than pretending Auto is a real provider model.

Recommended stored value:

```text
auto
```

The image model panel exposes:

```text
Auto
<all configured concrete image models>
```

Rules:

1. New sessions/default-reset state resolve to Auto when both Auto targets are valid.
2. A manually selected concrete model always wins.
3. Auto checks whether a usable active character reference exists.
4. If a reference exists, Auto resolves `image_auto.reference_model`.
5. If no reference exists, Auto resolves `image_auto.text_model`.
6. If the configured Auto reference target is unavailable, Auto may fall back to the configured Auto text target.
7. Auto must never select a provider/model not declared in the provider's enabled `image_models` list.
8. Manual model failures must not silently route to another model.

The UI may show the resolved model in explanatory text, but persistence remains either `auto` or the user's explicit concrete selection.

## Character reference source

Use the active session's `character_file` and the existing safe character-card resolver.

The reference loader must:

1. obtain the active character filename from the session;
2. resolve it through the existing safe character-card path helper;
3. require a regular PNG file under the configured character directory;
4. read the PNG bytes with the existing image-size ceiling enforced;
5. validate that the visible PNG image is decodable enough for upload;
6. return the original or safely normalized in-memory image bytes;
7. never persist an extracted portrait copy.

Important: a SillyTavern PNG card is both the visible character image and a metadata container. Reference generation uses the visible PNG pixels, not the embedded character metadata payload.

If embedded card metadata is corrupt but the PNG pixels are valid, the image may still be used as the visual reference. If the PNG pixels themselves are unusable, Auto treats the reference as unavailable.

## Reference prompt construction

The existing current-scene prompt builder remains responsible for turning committed roleplay state into a bounded visual prompt.

When a reference-capable route is used, wrap/augment the scene prompt with a short identity-preservation instruction. The instruction must distinguish identity from mutable scene attributes.

Required semantics:

```text
Use the supplied image as the visual identity reference for the primary character.

Preserve:
- facial identity
- hairstyle and distinctive physical traits
- apparent age
- established character design

Render the current-scene prompt below.
Allow pose, expression, clothing, environment, lighting, framing and camera angle
to change when the current scene requires it.
Do not merely recreate the reference portrait.

Current scene:
<bounded existing scene prompt>
```

The final prompt must obey the selected model's prompt limit. Step Image Edit 2 currently has a 512-character prompt ceiling, so the prompt builder must reserve space for the identity prefix before asking the Utility model for the scene portion.

Do not add explicit sexual content, age reinterpretation, or other content that is absent from the committed scene. The image prompt remains a visualizer of committed story state rather than a story-advancement mechanism.

## Image edit transport

NanoGPT currently exposes OpenAI-compatible image editing through:

```text
POST /api/v1/images/edits
```

with standard multipart image-edit fields including model, prompt, and image upload. The bridge should support this as the first reference transport.

The reference request must:

- use the provider's validated endpoint/base URL;
- reuse the provider credential/header policy;
- send exactly one active-character reference image in this feature;
- enforce the provider/reference upload byte ceiling before the request;
- bound the response size using the existing generated-image ceiling;
- accept the same safe response forms already handled by generation where applicable;
- avoid logging image bytes, data URLs, provider credentials, or raw sensitive upstream bodies.

The existing `/images/generations` JSON transport remains unchanged for text-only routes.

## Size behavior

Text-only generation continues to use the existing session size preset.

Reference/edit models may have different size semantics. For Step Image Edit 2, reference generation must not invent unsupported dimensions. The adapter should map the current bridge size setting only when the selected model/provider explicitly supports that size; otherwise use the provider/model's supported automatic/default edit sizing.

The UI should not promise a resolution the selected reference model cannot honor.

No destructive resize or crop of the character reference should be performed merely to satisfy the output size preference.

## Routing API boundaries

Keep responsibilities separated:

### Model/capability resolver

Responsible for:

- reading provider image model declarations;
- reading capability metadata;
- resolving Auto to one concrete provider/model;
- reporting whether the resolved route is text or reference.

It must not read character files or perform network requests.

### Character reference loader

Responsible for:

- resolving the active native character PNG safely;
- validating/loading its visible image bytes;
- returning reference bytes plus a safe MIME type or `None`.

It must not select models.

### Image transport

Responsible for:

- text generation request (`/images/generations`);
- reference edit request (`/images/edits`);
- response decoding and size validation.

It must not know session routing policy.

### `/imagine` orchestration

Responsible for:

- obtaining session image settings;
- determining reference availability;
- resolving Auto/manual routing;
- requesting the appropriate bounded prompt;
- dispatching to the correct transport;
- delivering the final image to Telegram.

## Error and fallback behavior

### Auto mode

- Missing active character PNG: use Auto text model.
- Unsafe/out-of-tree character path: treat reference as unavailable and use Auto text model.
- Corrupt PNG pixels: treat reference as unavailable and use Auto text model.
- Auto reference model is no longer configured: use Auto text model if valid.
- Auto text model is also unavailable: fail with the existing actionable image-provider configuration error.

### Manual model selection

- Manual text model + character portrait exists: generate text-only with the selected model.
- Manual reference model + usable portrait: send reference generation.
- Manual reference model + missing/unusable portrait: fail with a clear message that the selected model needs a usable character reference.
- Provider rejects the reference/edit request: report the bounded provider failure; do not silently spend another request on a different model.

### Telegram progress message

Preserve the existing UX:

1. create the progress message immediately before the provider call;
2. send the final image only on success;
3. remove the progress message after successful delivery;
4. on failure, ensure the progress indicator does not remain misleadingly stuck.

This feature must not reintroduce prompt/caption output alongside the final image.

## Security and privacy

- Continue using the existing provider endpoint validation / SSRF protections.
- Character file resolution must use the existing safe native-card path policy.
- Do not allow a character filename to select arbitrary filesystem paths.
- Do not persist temporary portrait copies.
- Do not include raw character image bytes, base64, or data URLs in logs.
- Do not include provider keys in logs or Telegram errors.
- Keep existing image and JSON response byte ceilings.
- Add a bounded upload ceiling for the reference image; it must not exceed the stricter of the bridge image limit and provider allowance.
- Reference bytes are sent only to the concrete image provider selected by the session routing policy.

## Configuration validation

At startup or provider-catalog load time, capability metadata should be validated conservatively:

- every `image_model_capabilities` key should correspond to a declared `image_models` entry;
- Auto model targets must point to declared models;
- Auto reference target must advertise `reference` or `both`;
- Auto text target must advertise `text` or `both`;
- invalid optional metadata should not make unrelated non-image Story/Utility providers unusable, but the affected image route must fail closed with an actionable configuration error.

## Testing requirements

At minimum, tests must cover:

1. Auto + valid active character PNG -> configured reference model.
2. Auto + missing character PNG -> configured text model.
3. Auto + unsafe character path -> text fallback without reading outside the character directory.
4. Auto + valid PNG pixels but invalid card metadata -> reference still usable.
5. Auto + corrupt PNG pixels -> text fallback.
6. Manual Chroma/text route remains text-only even with a character portrait.
7. Manual Step/reference route receives the character image.
8. Manual reference route without usable portrait fails clearly and does not call a text model.
9. Switching the session character changes the reference source on the next `/imagine`.
10. Existing provider configs with only string `image_models` behave unchanged.
11. Capability metadata for an undeclared model is rejected for the affected image route.
12. Auto reference target disappearing from config falls back only to the configured Auto text target.
13. Provider edit failure does not trigger a second paid generation.
14. Reference upload obeys byte ceilings.
15. Generated result obeys existing output byte ceilings.
16. Progress message is removed after success.
17. Progress message does not remain stuck after provider/delivery failure.
18. Final Telegram image has no prompt caption.
19. Step Image Edit 2 prompt construction respects its 512-character limit including the identity prefix.
20. Text-only Z Image Turbo retains its existing 1,200-character prompt limit.

## Documentation changes

Update:

- `docs/user-guide.md`: explain Auto image routing and character-reference behavior.
- `docs/configuration.md`: document optional image capability and Auto routing metadata.
- `/help imagine` canonical command text if the model-selection behavior is described there.
- provider example configuration, if one is maintained, without adding real credentials.

## Out of scope

This change does not:

- create a character-image database;
- train LoRAs or embeddings;
- perform face recognition;
- extract multiple sprites/expressions as references;
- send multiple character references for group scenes;
- infer identity from arbitrary Telegram uploads;
- automatically benchmark or rank image models;
- change story generation or character-card metadata;
- change SillyTavern's ownership of character files.

Multi-character/group reference generation can be designed separately after the single-active-character path is stable.

## Current external API assumptions

As verified on 2026-10-02:

- NanoGPT provides OpenAI-compatible `/api/v1/images/generations` and `/api/v1/images/edits` routes.
- The edit route accepts standard OpenAI-style multipart image uploads and also supports custom JSON reference workflows.
- `step-image-edit-2` accepts one reference/edit image and has a 512-character prompt limit.
- NanoGPT exposes live image-model discovery at `GET /api/v1/images/models`.

References:

- https://nano-gpt.com/blog/openai-compatible-image-clients
- https://nano-gpt.com/models/image/step-image-edit-2
