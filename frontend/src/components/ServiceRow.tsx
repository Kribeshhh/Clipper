export function ServiceRow({
  name,
  role,
  available,
}: {
  name: string;
  role: string;
  available: boolean | null;
}) {
  const label =
    available === null ? "Unknown" : available ? "Online" : "Offline";
  return (
    <li className="flex items-center justify-between gap-4 py-5">
      <div>
        <strong>{name}</strong>
        <p className="text-sm text-stone-600">{role}</p>
      </div>
      <span className={"pill " + label.toLowerCase()}>{label}</span>
    </li>
  );
}
