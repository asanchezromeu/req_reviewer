import React from "react";

export default class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("ReqFind UI error", error, info);
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <div className="min-h-screen bg-[#f5f7f8] p-8 text-slate-900">
        <div className="mx-auto max-w-3xl rounded-xl border border-red-200 bg-white p-6 shadow-sm">
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-red-700">
            Interface error
          </div>
          <h1 className="mt-2 text-2xl font-black">The page hit a rendering problem.</h1>
          <p className="mt-3 text-sm leading-6 text-neutral-700">
            The backend is still running. Refresh the page after rebuilding the frontend, or lower
            the matching threshold if the response contains no strong matches.
          </p>
          <pre className="mt-4 max-h-48 overflow-auto rounded-lg bg-slate-950 p-4 text-xs text-white">
            {String(this.state.error?.message || this.state.error)}
          </pre>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-5 rounded-md bg-teal-700 px-4 py-2 text-sm font-semibold text-white"
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
