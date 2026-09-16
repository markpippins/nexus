import * as React from "react";
import { X, Play, Pause, RotateCcw, Volume2, Sparkles, CheckCircle2 } from "lucide-react";
import { DocumentationEntry } from "@/core/runtime/documentationRegistry";

interface HelpVideoOverlayProps {
  isOpen: boolean;
  onClose: () => void;
  entry: DocumentationEntry;
}

export const HelpVideoOverlay: React.FC<HelpVideoOverlayProps> = ({ isOpen, onClose, entry }) => {
  const [isPlaying, setIsPlaying] = React.useState(true);
  const [progress, setProgress] = React.useState(25);
  const [activeChapter, setActiveChapter] = React.useState(0);

  const chapters = [
    { title: "1. Capability Contract Overview", time: "0:00" },
    { title: "2. Event Dispatching & Navigation", time: "0:45" },
    { title: "3. Inspector State Synchronization", time: "1:15" },
    { title: "4. Workflow Stepping & Quorum", time: "1:40" },
  ];

  React.useEffect(() => {
    if (!isOpen || !isPlaying) return;
    const timer = setInterval(() => {
      setProgress((p) => (p >= 100 ? 0 : p + 1));
    }, 200);
    return () => clearInterval(timer);
  }, [isOpen, isPlaying]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-md animate-in fade-in">
      <div
        className="w-full max-w-3xl rounded-xl border border-border bg-surface shadow-2xl overflow-hidden flex flex-col animate-in zoom-in-95 duration-200"
        role="dialog"
        aria-modal="true"
      >
        {/* Modal Header */}
        <div className="p-4 border-b border-border/60 bg-surface/90 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="rounded bg-signal/15 border border-signal/30 px-2 py-0.5 text-[10px] font-mono font-bold text-signal">
              VIDEO GUIDE
            </span>
            <span className="font-semibold text-foreground text-sm">
              {entry.videoTitle || `${entry.title} Walkthrough`}
            </span>
            <span className="text-xs text-muted-foreground">({entry.videoDuration || "2m"})</span>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-surface/80"
            aria-label="Close video guide"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Simulated Video Player */}
        <div className="relative aspect-video bg-black/95 flex flex-col justify-between p-6 overflow-hidden">
          {/* Animated Mock Interface Simulation */}
          <div className="absolute inset-0 flex flex-col items-center justify-center p-8 text-center space-y-4 opacity-90">
            <div className="p-4 rounded-xl border border-primary/40 bg-surface/90 shadow-2xl max-w-md w-full space-y-3">
              <div className="flex items-center justify-between border-b border-border/60 pb-2">
                <div className="flex items-center gap-2 font-mono text-xs text-primary font-bold">
                  <Sparkles className="h-4 w-4 text-primary" />
                  <span>{entry.capabilityContract || entry.title}</span>
                </div>
                <span className="text-[10px] font-mono text-muted-foreground">
                  SIMULATOR ACTIVE
                </span>
              </div>
              <div className="p-3 rounded bg-background/80 text-left font-mono text-[11px] text-foreground/90 space-y-1">
                <div className="text-accent font-semibold">
                  Chapter: {chapters[activeChapter].title}
                </div>
                <p className="text-muted-foreground font-sans text-xs">
                  {entry.summary.slice(0, 140)}...
                </p>
              </div>
              <div className="flex items-center justify-between text-[10px] font-mono text-muted-foreground pt-1">
                <span>ContractState: SYNCHRONIZED</span>
                <span className="text-primary font-bold">EventBus: LISTENING</span>
              </div>
            </div>
          </div>

          {/* Video Controls Bar (Overlay) */}
          <div className="relative mt-auto space-y-2 bg-gradient-to-t from-black/90 to-transparent p-3 rounded-lg">
            {/* Scrubber */}
            <div className="w-full bg-surface/60 h-1.5 rounded-full overflow-hidden cursor-pointer">
              <div
                className="bg-primary h-full transition-all duration-100"
                style={{ width: `${progress}%` }}
              />
            </div>

            <div className="flex items-center justify-between text-white text-xs">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setIsPlaying(!isPlaying)}
                  className="p-1.5 rounded hover:bg-white/10 transition-colors"
                >
                  {isPlaying ? (
                    <Pause className="h-4 w-4" />
                  ) : (
                    <Play className="h-4 w-4 fill-white" />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setProgress(0)}
                  className="p-1.5 rounded hover:bg-white/10 transition-colors"
                >
                  <RotateCcw className="h-4 w-4" />
                </button>
                <span className="font-mono text-[11px] text-zinc-400">
                  {Math.floor((progress * 1.2) / 60)}:
                  {String(Math.floor((progress * 1.2) % 60)).padStart(2, "0")} /{" "}
                  {entry.videoDuration || "2:00"}
                </span>
              </div>

              <div className="flex items-center gap-2 font-mono text-[11px] text-zinc-400">
                <Volume2 className="h-4 w-4" />
                <span>100%</span>
              </div>
            </div>
          </div>
        </div>

        {/* Chapter Selection Grid */}
        <div className="p-4 border-t border-border bg-surface grid grid-cols-2 sm:grid-cols-4 gap-2">
          {chapters.map((ch, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => {
                setActiveChapter(idx);
                setProgress(idx * 25);
              }}
              className={`p-2.5 rounded-lg border text-left text-xs transition-all ${
                activeChapter === idx
                  ? "border-primary bg-primary/10 text-primary font-bold shadow-xs"
                  : "border-border/60 bg-background/50 text-muted-foreground hover:text-foreground hover:bg-background"
              }`}
            >
              <div className="font-mono text-[10px] text-muted-foreground">{ch.time}</div>
              <div className="line-clamp-1 font-sans text-xs mt-0.5">{ch.title}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
