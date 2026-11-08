# JUICR

A local model/settings comparison workbench with a fresh browser interface. Python 3.11 or newer, no packages to install.

```powershell
python juicr_local.py
```

Opens http://127.0.0.1:8811 in your browser. Stop with Ctrl+C. Use `--port 9000`, `--no-browser`, or `--data PATH` as needed. Data is stored separately from the legacy platform, by default in `~/.juicr-local/workbench.db`.

## Command line for AI tools and scripts

Start JUICR first (`python juicr_local.py --no-browser` is fine). The CLI uses that app's connections and saved data. It returns JSON and needs no extra packages. Configure keys in Connections or in the **server's** environment; do not put keys in command arguments.

```powershell
# Discover available models from all configured endpoints (catalog reads only).
python juicr_local.py cli models list
python juicr_local.py cli models list --provider lemonade --search gemma
python juicr_local.py cli models list --provider openrouter --search qwen

# List saved tests and inspect an exact revision.
python juicr_local.py cli tests list
python juicr_local.py cli tests show TEST_ID

# Create a test from a source file; sampling settings are optional.
python juicr_local.py cli tests create --title "Fact extraction" --content-file source.txt --instructions "Extract the key facts" --task extraction --settings '{"temperature":0.2}'

# Review the frozen inputs and request/output allowance, then run.
python juicr_local.py cli tests run TEST_ID --provider lemonade --model MODEL_ID --dry-run
python juicr_local.py cli tests run TEST_ID --provider lemonade --model MODEL_ID
python juicr_local.py cli tests run TEST_ID --provider openrouter --model MODEL_ID --no-wait

# Inspect a submitted run, stop its queued requests, or compare saved answers.
python juicr_local.py cli runs show RUN_ID
python juicr_local.py cli runs cancel RUN_ID
python juicr_local.py cli results list
python juicr_local.py cli results compare RESULT_ID_1 RESULT_ID_2
```

Replace the uppercase IDs with values returned by earlier commands. Model listings report each endpoint's availability or error separately and do not imply a model is loaded. Use `--provider demo --model demo-model` for a simulated run. Actual Lemonade/OpenRouter runs generate output and may incur usage. Runs preserve the saved test's inputs/settings and append results visible in the web app.

`tests create --file draft.json` accepts the draft object returned by `tests show` (its `draft` field); `--file -` reads it from stdin. JSON is convenient for avoiding shell quoting differences. Add `--previous TEST_ID` to create a new immutable revision. File-based and individual draft-field arguments cannot be mixed. Text-based creation also supports `--context-file`, `--criteria`, `--repeats`, `--seed`, `--timeout`, `--writing-output-tokens` and `--extraction-output-tokens`. Sampling JSON accepts the same seven settings as the web app. Omitted settings use service defaults.

`tests list --all-versions` includes earlier revisions; `--search TEXT` filters titles. Runs accept `--url` for Lemonade or `--endpoint` for an OpenRouter provider pin. `--dry-run` does not save a run, track a new model or call inference. Waiting defaults to 600 seconds; `--wait-timeout SECONDS` changes only how long the CLI waits. Expiry or Ctrl+C leaves submitted work running: use the returned run ID (or web interface) to inspect/stop it. Stop cannot guarantee cancellation of an in-flight request. No automatic retries.

For another port, put the server option **before** the command: `python juicr_local.py cli --server http://127.0.0.1:9000 tests list`. `python juicr_cli.py` is an equivalent entry point. Use `cli --help` or a command's `--help` for details. Exit codes: 0 success; 1 failed/partial catalog or run; 2 invalid input/connection; 3 wait expired; 130 interrupted client. Standard output contains JSON results; errors use standard error. The server must run the current code; restart an older idle instance to enable the new test-list route.

## Start here

Use **Try a practice example** if you are new to the app. It uses simulated answers, with no model calls or charges. **Learn the basics in a minute** explains the difference between a test, a model and a result.

1. **Choose a test.** Pick a saved test or click Create a test. Give it a name, explain what the model should do, and paste your source material. Background and answer requirements are optional. Save it to continue.
2. **Run a model.** Choose Demo, Lemonade or OpenRouter, then choose a model from the searchable list. You can leave the saved settings alone. Review the summary and click Run test.
3. **Review results.** Every answer is saved. Read it against your requirements. Select two to six saved answers and click Compare selected to see them side by side. Comparing saved answers does not run a model or use credits. Download results exports the displayed evidence as JSON.

The three navigation buttons stay available as you move around. Existing tests, saved answers, older test versions and connections are retained.

## Theme and mode

The header has two independent choices:

- **Theme** changes colors: Default (the original palette), Lemonade (warm yellows), Espresso (dark browns), or Cyberpunk (dark blue with cyan and pink accents).
- **Mode** changes spacing and guidance: Novice keeps the full walkthrough, Learned uses tighter spacing and shorter explanations, and Expert provides a compact layout with minimal guidance. Detailed setting descriptions are still available in tooltips.

Your choices are remembered in this browser and survive a reload. They do not change tests, model settings, results or credentials. Errors, costs and deletion choices remain visible in every mode. New browser profiles start at Default / Novice.

## Learn settings when you need them

**Advanced model settings** is closed by default. Each setting has a plain-language explanation and accepted range. Empty sampling fields use the service's defaults, which may differ between models. New tests start with these defaults; existing saved tests keep their original values.

To experiment, change one setting at a time. If you change a saved test's settings, JUICR asks you to save a new test version before running it. This preserves the original test and its answers. A model's answer can vary between runs, even with the same seed.

**More options** contains repeats, the random seed, waiting limits and batches of up to six models. Each repeat is another request. You can run one model once without using any of these options. The output-token allowance is a maximum, not a desired answer length; reasoning may use some of it.

## More ways to compare

1. Enter shared instructions and source content; choose writing or extraction.
2. Configure one model. Select a connection to fetch its available models, type in Search models to filter by name or ID, then pick from the dropdown. Refresh fetches the catalog again; editing a Lemonade base URL reloads its list. Blank sampling fields omit that parameter. Optionally add or duplicate configurations for a batch of up to six.
3. Select repeats, starting seed and request timeout. Review the request count and output-token allowance, then **Run test**. A single model with one repeat makes one request.
4. Every response is saved automatically. Run more tests with any models/settings; earlier results remain. **Saved results** lists individual responses across runs and restarts, with filters for title, model, provider and settings.
5. Select two to six saved responses and click **Compare selected**. Review outputs, settings, output limits, seeds, timing, usage/cost and original inputs side by side without running inference again. Comparisons flag differences in source content, instructions or task. Use **View** for a single saved result. Export the displayed test or saved-result comparison as JSON.

Columns can use the same or different models and settings. Matched repeats send the same messages and seed; shuffled order is retained. Seeds do not guarantee reproducibility across models or providers. Demo is explicitly simulated and does not measure inference or quality.

## Reusable content/context tests

In step 1, create a test with source material and instructions. Open **Add background and answer requirements** for optional context and criteria. **Save test & choose a model** records an immutable test version. Requirements go to the model; they are not automatic scores. Advanced sampling, output limits, repeats, seed and timeout are available in step 2.

In step 1, open **See which models have tried each test**. Each saved test has a cell for each tracked model. To add a target without inference, select it in step 2 and choose **Add selected models to coverage** under More options. **No results · not run** identifies missing coverage. **Quick test** loads that version's source, context, criteria and settings into the indicated model and opens step 2. Nothing runs until you choose **Run test**.

Completed responses accumulate under their exact test version and model/service identity. **View answers** filters to that cell; **Compare repeats** opens its saved answers side by side. Failed attempts are separate from completed responses. Model focus, test search and **Include earlier test versions** help inspect coverage over time. Older one-off runs remain in Saved answers but do not count as performed reusable tests.

Changing content or parameters makes the form a modified test. Save a new revision to track those results; earlier test definitions and outputs stay intact. Different providers, Lemonade base URLs and pinned OpenRouter providers count as distinct model targets. Context is sent before source content, and criteria are included with the instructions.

## Delete tests or results

In step 1, click **Delete test** on a test card. The confirmation tells you how many saved versions and results belong to it. Results are kept by default. Select the checkbox to also delete its saved answers from every version.

In step 3, click **Delete** beside an answer, or select answers and choose **Delete selected**. Deleting answers does not delete their tests. Unrelated results are kept.

Use **Undo deletion** to restore deleted items. Undo remains available after a restart; you can undo earlier deletions in reverse order. Deleted data is hidden from the app and retained locally for recovery, rather than securely erased. Delete/Undo are unavailable while a test is running.

## Connections

Click **Connections & keys** in the top bar to enter API keys for Lemonade and OpenRouter. Paste a key in its masked field and click **Save key**, then **Test connection**. Keys take effect immediately and model lists refresh. Blank key fields preserve the current key. **Remove key** removes the entered/saved key; an environment fallback, if configured, still applies. Changes are blocked while a test is running.

By default, keys last until JUICR closes. On Windows, check **Remember on this computer** to store keys encrypted for your Windows account using DPAPI. Remembered keys are stored separately in `connections.json` under JUICR's data directory; they are not portable to another Windows account. Other platforms support session keys and environment variables. Fields are cleared after saving or closing the panel; stored keys are never filled back into the form. Re-enter a key to replace it or change its storage choice.

For Lemonade, use the configured API base URL (default `http://10.138.6.1:13305/api/v1`) and choose a discovered model. Different URLs must include `/api/v1` or `/v1`. Its configured key is sent only to that exact base URL. To change the server while a key exists, re-enter the appropriate key or remove the current one. JUICR sends request settings without explicit load/unload calls; Lemonade may auto-load a selected model. Model discovery and Test connection only read GET /models and do not load models or generate responses.

For OpenRouter, enter its key in Connections and choose a discovered model. Optionally pin a provider slug. Requests require parameter support and disable fallback; JUICR maps `repeat_penalty` to `repetition_penalty`. Identical model names across services do not establish identical weights, quantization, templates or defaults. Lemonade parameter support/effect remains unverified unless independently established.

`LEMONADE_API_KEY` and `OPENROUTER_API_KEY` remain optional environment fallbacks. A key entered in Connections takes precedence. Configured keys are never returned to the browser or included in exported request evidence. Content entered in cloud columns is sent to OpenRouter when you press Run. Saved setups and outputs are plain local data.

The displayed allowance covers requested output tokens, not input tokens or dollars. Reported cost may be unavailable. Configure an account/key spending limit with your provider if needed. Stop skips queued requests; the active non-streaming request may finish and incur usage. No automatic retries or resume. After interruption, usage of an in-flight request may be unknown.

## Verification

```powershell
python -W error::ResourceWarning -m unittest tests.test_local -v
python -W error::ResourceWarning -m unittest tests.test_cli -v
```

Uses injected responses and loopback HTTP; no live inference. The older app in `juicr/` and its pilot data remain available for historical reference. `python -m juicr` still launches that legacy app; use `python juicr_local.py` for this workbench.

Developers can also run `node tests/test_appearance.cjs` to check appearance preference behavior. Node is only needed for that verification, not for running the app.
