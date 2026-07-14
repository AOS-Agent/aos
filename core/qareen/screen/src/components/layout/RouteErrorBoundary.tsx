import { Component, type ReactNode } from 'react';
import { RotateCcw } from 'lucide-react';

// ---------------------------------------------------------------------------
// RouteErrorBoundary — catches render errors in any route so a single thrown
// exception (e.g. a null.toLowerCase()) can never take the whole app to a
// black screen. Renders a warm, honest fallback per DESIGN.md rule 4.
//
// Mounted per-route (keyed by pathname in Layout) so navigating away — or
// hitting Reload — clears the error and re-renders the target route fresh.
// ---------------------------------------------------------------------------

interface Props {
  children: ReactNode;
  onReset?: () => void;
}

interface State {
  error: Error | null;
}

export default class RouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    // Surface to the console for debugging; never rethrow.
    console.error('[RouteErrorBoundary] caught render error:', error);
  }

  handleReset = () => {
    this.setState({ error: null });
    this.props.onReset?.();
  };

  render() {
    if (this.state.error) {
      return (
        <div className="h-full flex flex-col items-center justify-center px-6 text-center font-sans">
          <p className="text-[18px] font-[600] text-text mb-2 font-serif tracking-[-0.01em]">
            This page hit a snag
          </p>
          <p className="text-[13px] text-text-tertiary max-w-[320px] leading-[1.6] mb-6">
            Something on this screen failed to render. The rest of the app is
            fine — reload the page or head back home.
          </p>
          <button
            onClick={this.handleReset}
            className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-full bg-bg-secondary/60 backdrop-blur-md border border-border-secondary text-[12px] font-[510] text-text-secondary hover:text-text hover:bg-bg-tertiary/70 transition-colors cursor-pointer"
            style={{ transitionDuration: 'var(--duration-fast)' }}
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reload page
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
