import { Inbox } from "lucide-react";

export default function EmptyState({
  title,
  detail,
}: {
  title: string;
  detail: string;
}) {
  return (
    <div className="empty-state">
      <Inbox size={28} />
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  );
}
