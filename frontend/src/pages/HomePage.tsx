import { Link } from "react-router-dom";

const cards = [
  {
    to: "/upload",
    title: "Upload audio",
    description:
      "Pick an audio file from your computer and start a new processing task.",
    cta: "Go to upload",
  },
  {
    to: "/audio",
    title: "Task list",
    description:
      "Browse all uploaded audio tasks and check their current status.",
    cta: "View tasks",
  },
];

export function HomePage() {
  return (
    <section className="space-y-10">
      <header className="space-y-3">
        <h1 className="text-3xl font-bold tracking-tight">music-ai</h1>
        <p className="max-w-prose text-slate-600">
          An AI-powered music processing playground. Upload an audio file, see
          the task appear in the list, and open the detail page to follow its
          progress.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        {cards.map((c) => (
          <Link
            key={c.to}
            to={c.to}
            className="group block rounded-lg border border-slate-200 bg-white p-6 transition-colors hover:border-slate-400 hover:bg-slate-50"
          >
            <h2 className="text-lg font-semibold text-slate-900">{c.title}</h2>
            <p className="mt-2 text-sm text-slate-600">{c.description}</p>
            <p className="mt-4 text-sm font-medium text-slate-900 group-hover:underline">
              {c.cta} →
            </p>
          </Link>
        ))}
      </div>
    </section>
  );
}
