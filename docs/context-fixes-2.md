# Context fixes, round 2 (3 Nov 2026)

Three questions about our own context cost, each checked against Claude Code's documentation (code.claude.com/docs, read 3 Nov 2026, Claude Code 2.1.317) and, where a setting exists, tested.

## 1. Fading old tool results

**There is no setting, environment variable or hook that drops or shortens tool results from earlier turns.** The documentation mentions Claude Code itself clearing old tool results only in passing (costs page, "Expected rebuilds") and gives no switch for it. Compaction is the only built-in way old output leaves a session.

What exists, and what we did with it:

- `bashOutputMaxChars` (settings reference, v2.1.261 or later): output past the limit goes to a file and Claude gets a preview of up to 2,000 characters plus the path. Default 30,000, allowed range 4,000 to 128,000. Set to 12,000 for the hub and the unattended `claude -p` folder through `hub_lean.py`. Lanes get the same line from `lane.sh`'s `--settings` JSON (added in the context-fixes-3 pull request).
- `ctx_tool_share.py` measures it: where tool output comes from, how much of it is stale, and what a cap would keep out.
- Not touched: the file read limit (`CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`) and the connector output limit (`MAX_MCP_OUTPUT_TOKENS`). A lower file-read limit makes Claude read the same file in several pieces, and a lower connector limit cuts off mail data.

Measured on 640 transcripts from the last 2 days (531 with tool results):

| | all sessions | the hub's 26 sessions |
|---|---|---|
| tool results, share of conversation text | 50% | 40% |
| tool output older than 20 turns at session end | 55% of tool output, 28% of text | 94% of tool output, 37% of text |
| command results over 12,000 characters | 127 of 9,511 | 31 of 3,928 |
| kept out of context by the 12,000 cap | 1.7 million characters (about 0.5 million tokens) | 0.46 million characters |

Why lanes do not recycle earlier because of stale output: a recycle costs a fresh start of about 56,000 tokens, so recycling a lane at 70,000 instead of 100,000 frees only about 14,000 tokens and the lane refills them in a few turns. Recycling at the line already throws away more than any fade could.

Left for the hub to decide: the hub's own `autoCompactWindow` is 400,000 and its median turn is 180,000. Compaction is the only fade, so a lower window (`/autocompact 250k` for one session) is the lever. It is a user-settings change this lane may not make.

## 2. Cheaper compaction

**Compaction cannot run on a cheaper model.** No setting or environment variable names a compaction model. The prompt-caching page says the summary comes from "a separate request" that "repeats your conversation's system prompt, tools, and the history being summarized". It reuses the session's cached prefix, which is why it has to be the session's own model (each model has its own cache, same page, "Switching models"). The summary request also inherits the session's thinking setting (context-window page, v2.1.198).

Idle compaction (compacts a long conversation once the prompt cache is about to expire; `idleCompaction`, on by default, 200,000-token threshold) is already on for interactive sessions.

Our own recycle (the lane writes `handoff.md`) by a cheaper subagent: not worth it. The lane already holds the context in a warm cache, where reading costs a tenth of the normal input price. A cheaper subagent would have to read the transcript uncached, so it only wins if its input price is under a tenth of the lane's, and no Haiku or Sonnet tier is. The hand-off is also about 4 KB of output.

## 3. Symbol lookup for code (language-server plugins)

Installed at user scope: `typescript-lsp` and `pyright-lsp` from `claude-plugins-official`, with their servers (`npm install -g typescript-language-server typescript pyright`). `claude plugin details` shows 0 tokens always-on for both. Lanes keep them: they are not in `LANE_PLUGINS_OFF`. Turn one off with `claude plugin disable typescript-lsp@claude-plugins-official`.

The plugins give Claude an `LSP` tool (go to definition, find references, hover, list symbols, search symbols) and a type-error line after each edit. In a headless session the tool is hidden behind a tool search, and Claude did not call it on its own in any of the three lookups below.

Measured with `claude -p` on Sonnet, same question, LSP tool allowed against LSP tool disallowed (Claude then uses grep and read):

| lookup | grep and read | LSP tool, asked to use it |
|---|---|---|
| ScriptHammer: where is `createLogger` defined, its types | 505 characters of tool output, 3 turns, 201,000 tokens re-sent, $0.064 | 189 characters, 7 turns, 476,000 tokens re-sent, $0.175. It stopped at a re-export and could not give the types. |
| Claude_Commandz: who calls `handoff_summary` | found 3 scripts and a test, 2 turns, $0.052 | `findReferences` returned the definition file only: it missed 3 callers, and the answer said there were none. 5 turns, $0.104 |

Result: no saving on these lookups, and one wrong answer. The tool output is small either way because `grep -n` already points at the line; the extra round trips each re-send the whole context. Keep the plugins for the type-error line after edits; do not count on find-references for "who calls this" in a folder with no project file for the language server (pyright only saw the open file). Raw runs are in the lane folder `2026-11-03-context-fixes-2`.
