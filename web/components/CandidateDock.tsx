import { useMemo } from "react";
import { SAMPLE_CANDIDATES } from "@/lib/candidates";
import type { CandidatePayload } from "@/lib/types";

function parsePayload(json: string): CandidatePayload | null {
  try {
    const v = JSON.parse(json);
    return v && typeof v === "object" && !Array.isArray(v) ? (v as CandidatePayload) : null;
  } catch {
    return null;
  }
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-md border border-line bg-parchment/70 px-2 py-1 font-mono text-[10.5px] text-ink-soft">
      {children}
    </span>
  );
}

export function CandidateDock({
  selectedId,
  json,
  jsonError,
  started,
  starting,
  explain,
  onSelect,
  onJsonChange,
  onStart,
  onExplainChange,
}: {
  selectedId: string;
  json: string;
  jsonError: string | null;
  started: boolean;
  starting: boolean;
  explain: boolean;
  onSelect: (id: string) => void;
  onJsonChange: (v: string) => void;
  onStart: () => void;
  onExplainChange: (v: boolean) => void;
}) {
  const payload = useMemo(() => parsePayload(json), [json]);
  const sample = SAMPLE_CANDIDATES.find((s) => s.id === selectedId);
  const member = payload?.member;
  const signals = payload?.signals;
  const missions = payload?.missions ?? [];
  const tally = {
    passed: missions.filter((m) => m.passed).length,
    failed: missions.filter((m) => m.passed === false && !m.skipped).length,
    skipped: missions.filter((m) => m.skipped).length,
  };

  const locked = started || starting;

  return (
    <aside className="flex flex-col gap-5">
      <div>
        <p className="font-mono text-[10.5px] uppercase tracking-[0.22em] text-ink-faint">
          Candidate
        </p>
        <div className="relative mt-2">
          <select
            value={selectedId}
            onChange={(e) => onSelect(e.target.value)}
            disabled={locked}
            className="w-full cursor-pointer appearance-none rounded-xl border border-line-strong bg-card px-4 py-3 pr-10 text-[14.5px] font-medium text-ink transition-colors hover:border-pine disabled:cursor-not-allowed disabled:opacity-55"
          >
            {SAMPLE_CANDIDATES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
            {selectedId === "custom" && <option value="custom">Custom - edited payload</option>}
          </select>
          <span
            aria-hidden
            className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2 text-[11px] text-ink-faint"
          >
            &#9662;
          </span>
        </div>
      </div>

      <div className="rounded-2xl border border-line bg-card px-5 py-4">
        {member || signals ? (
          <>
            <p className="font-display text-[20px] font-semibold leading-snug text-ink">
              {member?.name ?? "Unnamed candidate"}
            </p>
            <p className="mt-0.5 text-[13px] text-ink-soft">
              {[
                member?.jobRole,
                member?.yearsExperience != null ? `${member.yearsExperience} yrs` : null,
                member?.education,
              ]
                .filter(Boolean)
                .join(" · ") || "No profile details"}
            </p>
            {sample && <p className="mt-2 text-[12.5px] italic leading-snug text-ink-faint">{sample.blurb}</p>}
            <div className="mt-3 flex flex-wrap gap-1.5">
              {signals?.commitDays != null && <Chip>{signals.commitDays}-day commit streak</Chip>}
              {signals?.missionsCompleted != null && <Chip>{signals.missionsCompleted} missions done</Chip>}
              {signals?.missionsFirstTry != null && <Chip>{signals.missionsFirstTry} first-try</Chip>}
            </div>
            {missions.length > 0 && (
              <p className="mt-3 border-t border-line pt-2.5 font-mono text-[11px] text-ink-faint">
                record: <span className="text-pine">{tally.passed} passed</span>
                {" · "}
                <span className="text-rust">{tally.failed} failed</span>
                {" · "}
                <span className="text-amber">{tally.skipped} skipped</span>
              </p>
            )}
          </>
        ) : (
          <p className="text-[13.5px] leading-relaxed text-ink-soft">
            Payload isn&rsquo;t valid JSON yet - fix it below to see the profile summary.
          </p>
        )}
      </div>

      <details className="group rounded-2xl border border-line bg-card open:bg-parchment/40">
        <summary className="cursor-pointer list-none px-5 py-3.5 font-mono text-[10.5px] uppercase tracking-[0.18em] text-ink-soft transition-colors hover:text-pine [&::-webkit-details-marker]:hidden">
          <span className="mr-1.5 inline-block transition-transform duration-200 group-open:rotate-90">
            &#9656;
          </span>
          Edit payload (JSON)
        </summary>
        <div className="px-4 pb-4">
          <textarea
            value={json}
            onChange={(e) => onJsonChange(e.target.value)}
            disabled={locked}
            spellCheck={false}
            aria-label="Candidate payload JSON"
            className="h-60 w-full resize-y rounded-xl border border-line bg-card p-3 font-mono text-[11.5px] leading-relaxed text-ink disabled:opacity-55"
          />
          {jsonError && <p className="mt-2 text-[13px] leading-snug text-rust">{jsonError}</p>}
        </div>
      </details>

      <button
        onClick={onStart}
        disabled={locked}
        className="w-full rounded-xl bg-pine px-5 py-3.5 text-[15px] font-semibold tracking-wide text-paper shadow-[0_14px_30px_-16px_rgba(20,58,49,0.75)] transition-colors hover:bg-pine-deep disabled:cursor-not-allowed disabled:opacity-45"
      >
        {starting ? "Contacting the interviewer\u2026" : started ? "Interview in progress" : "Begin interview"}
      </button>

      <label className="flex cursor-pointer items-start gap-2.5 px-1 text-[13px] text-ink-soft">
        <input
          type="checkbox"
          checked={explain}
          onChange={(e) => onExplainChange(e.target.checked)}
          className="mt-0.5 h-4 w-4 accent-pine"
        />
        <span>
          Explain question choices
          <span className="block text-[11.5px] text-ink-faint">
            shows why the engine picked each day, under every question
          </span>
        </span>
      </label>

      <p className="border-t border-line px-1 pt-4 font-mono text-[11px] leading-relaxed text-ink-faint">
        8 questions minimum &middot; 4+ curriculum days &middot; written report at the end
      </p>
    </aside>
  );
}
