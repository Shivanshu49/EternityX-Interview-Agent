import { useEffect, useState } from "react";

function useElapsedSeconds() {
  const [s, setS] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setS((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return s;
}

const Dots = () => (
  <span className="inline-flex items-end gap-1" aria-hidden>
    {[0, 1, 2].map((i) => (
      <span
        key={i}
        className="h-1.5 w-1.5 animate-dot rounded-full bg-pine"
        style={{ animationDelay: `${i * 0.18}s` }}
      />
    ))}
  </span>
);

/** Lightweight indicator while the next question is being generated. */
export function ThinkingQuestion({ label }: { label: string }) {
  const elapsed = useElapsedSeconds();
  return (
    <div className="flex w-full animate-rise flex-col items-start" role="status" data-testid="thinking-question">
      <span className="mb-1.5 px-1 font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
        Interviewer
      </span>
      <div className="flex max-w-[85%] items-center gap-3 rounded-2xl rounded-tl-md border border-line bg-card px-5 py-3.5">
        <Dots />
        <span className="text-[14px] text-ink-soft">
          {elapsed > 15 ? "Still working - a long answer gets a close read." : label}
        </span>
      </div>
    </div>
  );
}

const REPORT_STEPS = [
  "Re-reading the full transcript",
  "Scoring every answer against the curriculum",
  "Weighing cohort signals against interview evidence",
  "Writing the narrative assessment",
];

/** Heavier, more descriptive state for the slow final feedback call. */
export function ThinkingReport({ answers }: { answers: number }) {
  const elapsed = useElapsedSeconds();
  const step = REPORT_STEPS[Math.min(Math.floor(elapsed / 8), REPORT_STEPS.length - 1)];
  return (
    <div
      className="w-full animate-rise rounded-2xl border border-line-strong bg-card p-6 shadow-[0_18px_44px_-28px_rgba(34,32,26,0.4)]"
      role="status"
      data-testid="thinking-report"
    >
      <div className="flex items-baseline justify-between gap-4">
        <span className="font-mono text-[10.5px] uppercase tracking-[0.22em] text-gold">
          Compiling interview report
        </span>
        <span className="font-mono text-[11px] tabular-nums text-ink-faint">{elapsed}s</span>
      </div>
      <p className="mt-3 font-display text-[19px] font-medium leading-snug text-ink">
        The interviewer is reviewing all {answers} answers and writing the full assessment.
      </p>
      <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-soft">
        This is the long call - roughly 6,000 tokens of report. It usually takes 20-45 seconds;
        please leave the page open.
      </p>
      <div className="mt-5 h-1 overflow-hidden rounded-full bg-parchment">
        <div className="h-full w-1/4 animate-sweep rounded-full bg-pine" />
      </div>
      <p className="mt-2.5 animate-soft-pulse font-mono text-[11px] uppercase tracking-[0.14em] text-ink-faint">
        {step}&hellip;
      </p>
    </div>
  );
}
