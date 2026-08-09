"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { postInterview } from "@/lib/api";
import { SAMPLE_CANDIDATES } from "@/lib/candidates";
import type { ChatMsg, Feedback } from "@/lib/types";
import { CandidateDock } from "@/components/CandidateDock";
import { ChatMessage } from "@/components/ChatMessage";
import { ErrorNotice } from "@/components/ErrorNotice";
import { FeedbackReport } from "@/components/FeedbackReport";
import { ThinkingQuestion, ThinkingReport } from "@/components/Thinking";

type Phase = "setup" | "starting" | "ready" | "waiting" | "finalizing" | "done";

interface UiError {
  text: string;
  retry?: "start" | { message: string };
}

const pretty = (v: unknown) => JSON.stringify(v, null, 2);
const makeSessionId = () => `ix-${crypto.randomUUID().slice(0, 13)}`;

/** The backend finishes after >=8 questions (and >=4 curriculum days). */
const EXPECTED_QUESTIONS = 8;

export default function Home() {
  const [selectedId, setSelectedId] = useState(SAMPLE_CANDIDATES[0].id);
  const [json, setJson] = useState(() => pretty(SAMPLE_CANDIDATES[0].payload));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [explain, setExplain] = useState(true);

  const [phase, setPhase] = useState<Phase>("setup");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [error, setError] = useState<UiError | null>(null);
  const [draft, setDraft] = useState("");

  // Random values can't be part of the first render: the page is prerendered on
  // the server, and a mismatched id would break hydration.
  const [sessionId, setSessionId] = useState<string | null>(null);
  useEffect(() => setSessionId(makeSessionId()), []);

  const idRef = useRef(0);
  const nextId = () => ++idRef.current;
  // Incremented on reset so responses from an abandoned session are ignored.
  const runRef = useRef(0);

  const composerRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const reportRef = useRef<HTMLDivElement>(null);

  const questionsAsked = useMemo(
    () => messages.filter((m) => m.role === "interviewer").length,
    [messages],
  );
  const answersSent = useMemo(
    () => messages.filter((m) => m.role === "candidate").length,
    [messages],
  );
  const candidateMeta = useMemo(() => {
    try {
      const p = JSON.parse(json) as { member?: { name?: string; jobRole?: string } };
      return { name: p?.member?.name, role: p?.member?.jobRole };
    } catch {
      return {};
    }
  }, [json]);

  const starting = phase === "starting";
  const started = phase !== "setup" && phase !== "starting";
  const busy = phase === "waiting" || phase === "finalizing";

  useEffect(() => {
    if (feedback) reportRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    else endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, phase, feedback]);

  useEffect(() => {
    if (phase === "ready") composerRef.current?.focus();
  }, [phase]);

  useEffect(() => {
    if (draft === "" && composerRef.current) composerRef.current.style.height = "auto";
  }, [draft]);

  async function start() {
    if (phase !== "setup" || !sessionId) return;
    let candidate: unknown;
    try {
      candidate = JSON.parse(json);
    } catch (e) {
      setJsonError(`Candidate payload is not valid JSON: ${(e as Error).message}`);
      return;
    }
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
      setJsonError("Candidate payload must be a JSON object.");
      return;
    }
    setJsonError(null);
    setError(null);
    setPhase("starting");
    const run = runRef.current;
    try {
      const res = await postInterview({ sessionId, candidate }, { explain, timeoutMs: 150_000 });
      if (runRef.current !== run) return;
      setMessages([{ id: nextId(), role: "interviewer", text: res.reply, trace: res.trace }]);
      setPhase("ready");
    } catch (err) {
      if (runRef.current !== run) return;
      setPhase("setup");
      // The server discards a session whose opening question failed; a fresh id
      // also sidesteps any 409 if it did not.
      setSessionId(makeSessionId());
      setError({ text: (err as Error).message, retry: "start" });
    }
  }

  async function sendAnswer(text: string) {
    const message = text.trim();
    if (!message || phase !== "ready" || !sessionId) return;
    const expectFinal = questionsAsked >= EXPECTED_QUESTIONS;
    setError(null);
    setMessages((m) => [...m, { id: nextId(), role: "candidate", text: message }]);
    setDraft("");
    setPhase(expectFinal ? "finalizing" : "waiting");
    const run = runRef.current;
    try {
      const res = await postInterview(
        { sessionId, message },
        { explain, timeoutMs: expectFinal ? 300_000 : 150_000 },
      );
      if (runRef.current !== run) return;
      if (res.done) {
        setFeedback(res.feedback ?? null);
        // On the final turn `reply` is a placeholder ("Interview completed.");
        // only fall back to it if the server sent no feedback object.
        if (!res.feedback && res.reply) {
          setMessages((m) => [...m, { id: nextId(), role: "interviewer", text: res.reply }]);
        }
        setPhase("done");
      } else {
        setMessages((m) => [
          ...m,
          { id: nextId(), role: "interviewer", text: res.reply, trace: res.trace },
        ]);
        setPhase("ready");
      }
    } catch (err) {
      if (runRef.current !== run) return;
      // The server commits a turn only on success, so it is safe to retry.
      // Roll back the optimistic bubble and hand the answer back.
      setMessages((m) => m.slice(0, -1));
      setDraft(message);
      setPhase("ready");
      setError({ text: (err as Error).message, retry: { message } });
    }
  }

  function reset() {
    runRef.current += 1;
    setMessages([]);
    setFeedback(null);
    setError(null);
    setDraft("");
    setJsonError(null);
    setPhase("setup");
    setSessionId(makeSessionId());
  }

  function handleSelect(id: string) {
    const sample = SAMPLE_CANDIDATES.find((s) => s.id === id);
    if (!sample) return;
    setSelectedId(id);
    setJson(pretty(sample.payload));
    setJsonError(null);
  }

  function handleRetry() {
    if (!error?.retry) return;
    if (error.retry === "start") start();
    else sendAnswer(error.retry.message);
  }

  let q = 0;
  const renderedMessages = messages.map((m) => {
    if (m.role === "interviewer") {
      q += 1;
      const badge = `Interviewer · Q${q}${m.trace?.is_follow_up ? " · follow-up" : ""}`;
      return <ChatMessage key={m.id} msg={m} badge={badge} />;
    }
    return <ChatMessage key={m.id} msg={m} />;
  });

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-30 border-b border-line bg-paper/95 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-6xl items-center gap-4 px-5 py-3">
          <div className="flex items-baseline gap-2.5">
            <span className="font-display text-[21px] font-semibold tracking-tight text-ink">
              EternityX
            </span>
            <span className="hidden font-mono text-[10px] uppercase tracking-[0.24em] text-ink-faint sm:inline">
              Interview Agent
            </span>
          </div>
          <div className="flex-1" />
          {started && phase !== "done" && (
            <span className="font-mono text-[11.5px] tabular-nums text-ink-soft">
              {phase === "finalizing" ? (
                <span className="animate-soft-pulse text-gold">writing report&hellip;</span>
              ) : (
                <>
                  Question {questionsAsked}
                  <span className="text-ink-faint"> / {EXPECTED_QUESTIONS} expected</span>
                </>
              )}
            </span>
          )}
          {phase === "done" && (
            <span className="rounded-full bg-pine-soft px-3 py-1 font-mono text-[10.5px] uppercase tracking-[0.14em] text-pine">
              Report ready
            </span>
          )}
          {sessionId && (
            <span className="hidden font-mono text-[11px] text-ink-faint md:inline">{sessionId}</span>
          )}
          {(started || starting) && (
            <button
              onClick={reset}
              className="rounded-lg border border-line-strong px-3.5 py-1.5 text-[13.5px] font-medium text-ink-soft transition-colors hover:border-pine hover:text-pine"
            >
              New interview
            </button>
          )}
        </div>
      </header>

      <main className="mx-auto grid w-full max-w-6xl flex-1 gap-8 px-5 py-8 lg:grid-cols-[352px_minmax(0,1fr)]">
        <div className="lg:sticky lg:top-[76px] lg:self-start">
          <CandidateDock
            selectedId={selectedId}
            json={json}
            jsonError={jsonError}
            started={started}
            starting={starting}
            explain={explain}
            onSelect={handleSelect}
            onJsonChange={(v) => {
              setJson(v);
              setSelectedId("custom");
            }}
            onStart={start}
            onExplainChange={setExplain}
          />
        </div>

        <section className="flex min-w-0 flex-col">
          <div className="flex-1 space-y-5 pb-4">
            {messages.length === 0 && phase === "setup" && (
              <div className="flex animate-rise flex-col items-center rounded-3xl border border-dashed border-line-strong bg-card/60 px-8 py-16 text-center sm:py-24">
                <span className="font-mono text-[10.5px] uppercase tracking-[0.26em] text-gold">
                  Adaptive technical interview
                </span>
                <h1 className="mt-4 max-w-md font-display text-[32px] font-semibold leading-[1.15] tracking-tight text-ink">
                  Every question starts from the record.
                </h1>
                <p className="mt-4 max-w-md text-[14.5px] leading-relaxed text-ink-soft">
                  The interviewer reads the candidate&rsquo;s 31-day cohort history - what they
                  aced, retried, or avoided - and probes exactly there. Answer{" "}
                  {EXPECTED_QUESTIONS} questions and it writes a full assessment.
                </p>
                <p className="mt-6 font-mono text-[11px] uppercase tracking-[0.18em] text-ink-faint">
                  Pick a candidate, then Begin interview
                </p>
              </div>
            )}

            {renderedMessages}

            {starting && (
              <ThinkingQuestion label="Reading the cohort record and choosing where to begin&hellip;" />
            )}
            {phase === "waiting" && <ThinkingQuestion label="Reading your answer&hellip;" />}
            {phase === "finalizing" && <ThinkingReport answers={answersSent} />}

            {error && (
              <ErrorNotice
                text={error.text}
                onRetry={error.retry ? handleRetry : undefined}
                onDismiss={() => setError(null)}
              />
            )}

            {feedback && (
              <div ref={reportRef} className="scroll-mt-24 pt-2">
                <FeedbackReport
                  feedback={feedback}
                  candidateName={candidateMeta.name}
                  candidateRole={candidateMeta.role}
                  sessionId={sessionId}
                />
              </div>
            )}
            <div ref={endRef} className="scroll-mb-36" />
          </div>

          {started && (
            <div className="sticky bottom-0 -mx-1 bg-gradient-to-t from-paper via-paper/95 to-transparent px-1 pb-5 pt-8">
              {phase === "done" ? (
                <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-pine/25 bg-pine-soft px-5 py-4">
                  <p className="text-[14.5px] text-pine-deep">
                    <strong className="font-semibold">Interview complete.</strong> The written
                    report is above.
                  </p>
                  <button
                    onClick={reset}
                    className="rounded-xl bg-pine px-4 py-2 text-[14px] font-semibold text-paper transition-colors hover:bg-pine-deep"
                  >
                    Start another
                  </button>
                </div>
              ) : (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    sendAnswer(draft);
                  }}
                >
                  <div className="flex items-end gap-2 rounded-2xl border border-line-strong bg-card p-2 shadow-[0_18px_44px_-26px_rgba(34,32,26,0.45)] transition-colors focus-within:border-pine">
                    <textarea
                      ref={composerRef}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onInput={(e) => {
                        const el = e.currentTarget;
                        el.style.height = "auto";
                        el.style.height = `${Math.min(el.scrollHeight, 176)}px`;
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          sendAnswer(draft);
                        }
                      }}
                      rows={1}
                      disabled={busy}
                      placeholder={
                        phase === "finalizing"
                          ? "The interviewer is writing your report\u2026"
                          : phase === "waiting"
                            ? "The interviewer is thinking\u2026"
                            : "Answer the interviewer\u2026"
                      }
                      aria-label="Your answer"
                      className="max-h-44 min-h-[46px] flex-1 resize-none bg-transparent px-3 py-2.5 text-[15px] leading-relaxed text-ink placeholder:text-ink-faint focus:outline-none disabled:opacity-50"
                    />
                    <button
                      type="submit"
                      disabled={busy || !draft.trim()}
                      className="rounded-xl bg-pine px-5 py-2.5 text-[14.5px] font-semibold text-paper transition-colors hover:bg-pine-deep disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      Send
                    </button>
                  </div>
                  <p className="mt-2 text-center font-mono text-[10.5px] uppercase tracking-[0.16em] text-ink-faint">
                    Enter to send &middot; Shift+Enter for a new line
                  </p>
                </form>
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
