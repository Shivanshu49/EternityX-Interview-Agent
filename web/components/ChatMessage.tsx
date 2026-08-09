import type { ChatMsg, Trace } from "@/lib/types";

function TraceNote({ trace }: { trace: Trace }) {
  const chips: string[] = [];
  if (trace.day) chips.push(`day ${trace.day}${trace.day_title ? ` · ${trace.day_title}` : ""}`);
  if (trace.tier || trace.pattern) chips.push([trace.tier, trace.pattern].filter(Boolean).join(" / "));
  if (trace.move) chips.push(trace.move);
  if (trace.days_covered?.length) chips.push(`covered: ${trace.days_covered.join(", ")}`);

  return (
    <details className="group mt-1.5 max-w-[85%]">
      <summary className="cursor-pointer list-none font-mono text-[10.5px] uppercase tracking-[0.16em] text-ink-faint transition-colors hover:text-pine [&::-webkit-details-marker]:hidden">
        <span className="mr-1 inline-block transition-transform duration-200 group-open:rotate-90">&#9656;</span>
        Why this question
      </summary>
      <div className="mt-2 rounded-xl border border-dashed border-line-strong bg-parchment/70 px-4 py-3">
        {trace.reason && (
          <p className="mb-2.5 text-[13px] leading-relaxed text-ink-soft">{trace.reason}</p>
        )}
        <div className="flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <span
              key={c}
              className="rounded-md border border-line bg-card px-1.5 py-0.5 font-mono text-[10.5px] text-ink-soft"
            >
              {c}
            </span>
          ))}
        </div>
      </div>
    </details>
  );
}

export function ChatMessage({ msg, badge }: { msg: ChatMsg; badge?: string }) {
  const isYou = msg.role === "candidate";
  return (
    <div
      data-testid={`msg-${msg.role}`}
      className={`flex w-full animate-rise flex-col ${isYou ? "items-end" : "items-start"}`}
    >
      <span className="mb-1.5 px-1 font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
        {isYou ? "You" : (badge ?? "Interviewer")}
      </span>
      <div
        className={
          isYou
            ? "max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-pine px-5 py-3.5 text-[15px] leading-relaxed text-paper shadow-[inset_0_1px_0_rgba(255,255,255,0.09)]"
            : "max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tl-md border border-line bg-card px-5 py-3.5 text-[15px] leading-relaxed text-ink shadow-[0_2px_10px_-6px_rgba(34,32,26,0.18)]"
        }
      >
        {msg.text}
      </div>
      {msg.trace && <TraceNote trace={msg.trace} />}
    </div>
  );
}
