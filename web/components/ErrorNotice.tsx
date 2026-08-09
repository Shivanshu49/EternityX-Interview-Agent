export function ErrorNotice({
  text,
  onRetry,
  onDismiss,
}: {
  text: string;
  onRetry?: () => void;
  onDismiss?: () => void;
}) {
  return (
    <div
      role="alert"
      className="mx-auto w-full max-w-xl animate-rise rounded-xl border border-rust/30 bg-rust-soft px-5 py-4 text-center"
    >
      <p className="text-[14px] leading-relaxed text-rust">{text}</p>
      {(onRetry || onDismiss) && (
        <div className="mt-3 flex items-center justify-center gap-3">
          {onRetry && (
            <button
              onClick={onRetry}
              className="rounded-lg bg-rust px-4 py-1.5 text-[13.5px] font-semibold text-paper transition-opacity hover:opacity-85"
            >
              Retry
            </button>
          )}
          {onDismiss && (
            <button
              onClick={onDismiss}
              className="rounded-lg border border-rust/35 px-4 py-1.5 text-[13.5px] font-medium text-rust transition-colors hover:bg-rust/10"
            >
              Dismiss
            </button>
          )}
        </div>
      )}
    </div>
  );
}
