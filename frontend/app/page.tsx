"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { AnimatePresence } from "framer-motion";
import { GameCanvas } from "@/components/lab/game-canvas";
import { HUDHeader, SidePanel, AgentDialog, DealOverlay } from "@/components/lab/hud";
import {
  type Agent,
  type Deal,
  initialAgents,
  initialDeal,
  pipelineSteps,
} from "@/lib/lab-data";

export default function LabDashboard() {
  const [agents, setAgents] = useState<Agent[]>(initialAgents);
  const [deal, setDeal] = useState<Deal>(initialDeal);
  const [sidePanelOpen, setSidePanelOpen] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [showDealOverlay, setShowDealOverlay] = useState(false);
  const [currentStep, setCurrentStep] = useState(-1);
  const [isSimulating, setIsSimulating] = useState(true);
  const mapRef = useRef<HTMLDivElement>(null);
  const timeoutsRef = useRef<NodeJS.Timeout[]>([]);

  // Clear all pending timeouts
  const clearAllTimeouts = useCallback(() => {
    timeoutsRef.current.forEach(clearTimeout);
    timeoutsRef.current = [];
  }, []);

  const addTimeout = useCallback((fn: () => void, delay: number) => {
    const id = setTimeout(fn, delay);
    timeoutsRef.current.push(id);
    return id;
  }, []);

  // Pipeline simulation
  useEffect(() => {
    if (!isSimulating) return;

    const runStep = (stepIndex: number) => {
      if (stepIndex >= pipelineSteps.length) {
        // Reset simulation after a pause
        addTimeout(() => {
          setAgents(initialAgents);
          setDeal(initialDeal);
          setCurrentStep(-1);
          runStep(0);
        }, 6000);
        return;
      }

      const step = pipelineSteps[stepIndex];
      setCurrentStep(stepIndex);

      // Set agents to "running"
      setAgents((prev) =>
        prev.map((a) => {
          if (step.agentIds.includes(a.id)) {
            return {
              ...a,
              status: "running" as const,
              task: step.tasks[a.id] || a.task,
            };
          }
          // Previous running agents become completed
          if (a.status === "running") {
            return { ...a, status: "completed" as const };
          }
          return a;
        })
      );

      // Update deal
      setDeal((prev) => ({
        ...prev,
        status: step.dealStatus,
        currentPhase: step.dealPhase,
        progress: step.dealProgress,
        gates: step.gates,
      }));

      // After duration, mark as completed and move to next step
      addTimeout(() => {
        setAgents((prev) =>
          prev.map((a) => {
            if (step.agentIds.includes(a.id)) {
              return {
                ...a,
                status: "completed" as const,
                logs: [...a.logs, step.logs[a.id] || "Task completed"],
              };
            }
            // Previous completed agents go back to idle
            if (a.status === "completed" && !step.agentIds.includes(a.id)) {
              return { ...a, status: "idle" as const, task: null };
            }
            return a;
          })
        );

        // Schedule next step
        addTimeout(() => {
          runStep(stepIndex + 1);
        }, 1500);
      }, 4000);
    };

    // Start the first step after initial delay
    addTimeout(() => {
      runStep(0);
    }, 2000);

    return () => clearAllTimeouts();
  }, [isSimulating, addTimeout, clearAllTimeouts]);

  const handleAgentClick = useCallback((agent: Agent) => {
    setSelectedAgent(agent);
  }, []);

  const handleAgentFocus = useCallback(
    (agent: Agent) => {
      handleAgentClick(agent);
    },
    [handleAgentClick]
  );

  const activeDeals = 1;

  return (
    <div
      className="relative w-screen h-screen overflow-hidden"
      style={{ backgroundColor: "#1a1a1a" }}
    >
      {/* HUD Header */}
      <HUDHeader deal={deal} activeDeals={activeDeals} />

      {/* Lab Map */}
      <div className="pt-10">
        <GameCanvas
          agents={agents}
          deal={deal}
          onAgentClick={handleAgentClick}
          onBlackboardClick={() => setShowDealOverlay(true)}
          mapRef={mapRef}
        />
      </div>

      {/* Side Panel */}
      <SidePanel
        isOpen={sidePanelOpen}
        onToggle={() => setSidePanelOpen(!sidePanelOpen)}
        agents={agents}
        onAgentClick={handleAgentClick}
        onAgentFocus={handleAgentFocus}
      />

      {/* Agent Dialog */}
      <AnimatePresence>
        {selectedAgent && (
          <AgentDialog
            agent={selectedAgent}
            onClose={() => setSelectedAgent(null)}
          />
        )}
      </AnimatePresence>

      {/* Deal Overlay */}
      <AnimatePresence>
        {showDealOverlay && (
          <DealOverlay deal={deal} onClose={() => setShowDealOverlay(false)} />
        )}
      </AnimatePresence>

      {/* Simulation controls */}
      <div
        className="fixed bottom-4 left-4 z-50 flex items-center"
        style={{
          fontFamily: "'Press Start 2P', monospace",
          gap: 8,
        }}
      >
        <button
          className="cursor-pointer"
          style={{
            fontSize: 6,
            color: "#F0EBE0",
            backgroundColor: "rgba(30,30,30,0.9)",
            border: "2px solid #4A3F35",
            padding: "4px 8px",
          }}
          onClick={() => setIsSimulating(!isSimulating)}
        >
          {isSimulating ? "PAUSE" : "PLAY"}
        </button>
        <span
          style={{
            fontSize: 5,
            color: "#888",
            backgroundColor: "rgba(30,30,30,0.7)",
            padding: "2px 6px",
            border: "1px solid #444",
          }}
        >
          STEP {currentStep + 1}/{pipelineSteps.length}
        </span>
      </div>
    </div>
  );
}
