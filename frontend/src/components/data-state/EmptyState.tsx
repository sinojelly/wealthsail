import { Inbox, type LucideIcon } from "lucide-react";

export default function EmptyState({
  title,
  detail,
  icon: Icon = Inbox,
  className = "",
}: {
  title: string;
  detail: string;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <div
      className={["empty-state", className].filter(Boolean).join(" ")}
      role="status"
      aria-label={title}
    >
      <span className="empty-state-icon" aria-hidden="true">
        <Icon size={18} />
      </span>
      <div className="empty-state-copy">
        <strong>{title}</strong>
        <p>{detail}</p>
      </div>
    </div>
  );
}
