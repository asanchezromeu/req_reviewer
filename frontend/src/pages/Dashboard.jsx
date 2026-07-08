import ShowcaseWorkspace from "@/components/ShowcaseWorkspace";

const Logo = () => (
  <div className="flex items-center gap-3">
    <div className="app-logo-mark grid h-9 w-9 place-items-center text-lg font-black leading-none text-white">
      R
    </div>
    <div className="leading-none">
      <div className="text-[1.05rem] font-black tracking-tight">ReqFind</div>
      <div className="text-[10px] uppercase tracking-[0.14em] text-neutral-500">
        Local requirements intelligence
      </div>
    </div>
  </div>
);

export default function Dashboard() {
  return (
    <div className="min-h-screen bg-[#f5f7f8]">
      <header className="sticky top-0 z-50 border-b bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-16 w-full max-w-7xl items-center justify-between px-6">
          <Logo />
          <div className="text-xs uppercase tracking-[0.14em] text-neutral-500">
            Raspberry Pi · Ollama · Local data
          </div>
        </div>
      </header>

      <section className="hero-strip border-b">
        <div className="mx-auto w-full max-w-7xl px-6 py-9">
          <div className="mb-3 text-xs font-semibold uppercase tracking-[0.14em] text-teal-700">
            Requirements showcase
          </div>
          <h1 className="max-w-4xl text-4xl font-black tracking-tight text-slate-900 sm:text-5xl">
            Find the right requirement. Explain the bigger picture.
          </h1>
          <p className="mt-4 max-w-3xl text-base leading-relaxed text-neutral-600">
            Semantic search returns the closest requirement without filler. Executive mode grounds
            a concise summary in the ten most relevant requirements.
          </p>
        </div>
      </section>

      <main className="mx-auto w-full max-w-7xl px-6 py-8">
        <ShowcaseWorkspace />
      </main>
    </div>
  );
}
