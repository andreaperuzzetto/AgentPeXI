"use client";

import { motion, AnimatePresence } from "framer-motion";
import type { Agent, Deal, Zone } from "@/lib/lab-data";
import { ZONE_LABELS } from "@/lib/lab-data";

interface HUDHeaderProps {
  deal: Deal;
  activeDeals: number;
}

const phases: { key: Zone; label: string; abbr: string }[] = [
  { key: "discovery", label: "Discovery", abbr: "DISC" },
  { key: "proposal", label: "Proposal", abbr: "PROP" },
  { key: "delivery", label: "Delivery", abbr: "DLVR" },
  { key: "post_sale", label: "Post-Sale", abbr: "POST" },
];

export function HUDHeader({ deal, activeDeals }: HUDHeaderProps) {
  return (
    <div
      className="fixed top-0 left-0 right-0 z-50 flex items-center justify-between px-4 py-2"
      style={{
        backgroundColor: "rgba(30, 30, 30, 0.9)",
        borderBottom: "3px solid #4A3F35",
        fontFamily: "'Press Start 2P', monospace",
      }}
    >
      <div className="flex items-center" style={{ gap: 8 }}>
        <div
          className="px-2 py-1"
          style={{
            fontSize: 10,
            color: "#C0392B",
            border: "2px solid #C0392B",
            backgroundColor: "rgba(192, 57, 43, 0.1)",
          }}
        >
          AgentPeXI Lab
        </div>
      </div>

      <div className="flex items-center" style={{ gap: 4 }}>
        {phases.map((phase, i) => (
          <div key={phase.key} className="flex items-center">
            <div
              className="px-1.5 py-0.5"
              style={{
                fontSize: 6,
                color: deal.currentPhase === phase.key ? "#F4D03F" : "#888",
                backgroundColor:
                  deal.currentPhase === phase.key
                    ? "rgba(244, 208, 63, 0.15)"
                    : "transparent",
                border:
                  deal.currentPhase === phase.key
                    ? "1px solid #F4D03F"
                    : "1px solid transparent",
              }}
            >
              {phase.abbr}
            </div>
            {i < phases.length - 1 && (
              <span style={{ fontSize: 6, color: "#555", margin: "0 2px" }}>{">"}</span>
            )}
          </div>
        ))}
      </div>

      <div
        className="flex items-center"
        style={{
          fontSize: 7,
          color: "#A5D6A7",
          gap: 6,
        }}
      >
        <span>DEALS:</span>
        <span style={{ color: "#F4D03F" }}>{activeDeals}</span>
      </div>
    </div>
  );
}

interface SidePanelProps {
  isOpen: boolean;
  onToggle: () => void;
  agents: Agent[];
  onAgentClick: (agent: Agent) => void;
  onAgentFocus: (agent: Agent) => void;
}

const statusColors: Record<string, string> = {
  idle: "#888",
  running: "#4CAF50",
  pending: "#FFC107",
  blocked: "#F44336",
  completed: "#2196F3",
};

export function SidePanel({ isOpen, onToggle, agents, onAgentClick, onAgentFocus }: SidePanelProps) {
  return (
    <>
      {/* Toggle button */}
      <button
        className="fixed z-50 cursor-pointer"
        style={{
          right: isOpen ? 264 : 8,
          top: 52,
          width: 28,
          height: 28,
          backgroundColor: "rgba(30, 30, 30, 0.9)",
          border: "2px solid #4A3F35",
          color: "#F0EBE0",
          fontFamily: "'Press Start 2P', monospace",
          fontSize: 8,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          transition: "right 0.3s ease",
        }}
        onClick={onToggle}
      >
        {isOpen ? ">" : "<"}
      </button>

      {/* Panel */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            className="fixed top-10 right-0 bottom-0 z-40 overflow-y-auto"
            style={{
              width: 260,
              backgroundColor: "rgba(30, 30, 30, 0.95)",
              borderLeft: "3px solid #4A3F35",
              fontFamily: "'Press Start 2P', monospace",
            }}
            initial={{ x: 260 }}
            animate={{ x: 0 }}
            exit={{ x: 260 }}
            transition={{ type: "tween", duration: 0.3 }}
          >
            <div className="p-3">
              <div style={{ fontSize: 7, color: "#F4D03F", marginBottom: 12 }}>
                AGENTS
              </div>
              {agents.map((agent) => (
                <div
                  key={agent.id}
                  className="mb-2 cursor-pointer"
                  style={{
                    padding: 6,
                    backgroundColor: "rgba(255,255,255,0.05)",
                    border: "1px solid #444",
                  }}
                  onClick={() => onAgentFocus(agent)}
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center" style={{ gap: 6 }}>
                      <div
                        className="rounded-full"
                        style={{
                          width: 6,
                          height: 6,
                          backgroundColor: statusColors[agent.status],
                          boxShadow: `0 0 4px ${statusColors[agent.status]}`,
                        }}
                      />
                      <span style={{ fontSize: 6, color: agent.accent }}>
                        {agent.name}
                      </span>
                    </div>
                    <span
                      style={{
                        fontSize: 5,
                        color: statusColors[agent.status],
                        textTransform: "uppercase",
                      }}
                    >
                      {agent.status}
                    </span>
                  </div>
                  {agent.task && (
                    <div
                      style={{
                        fontSize: 5,
                        color: "#aaa",
                        lineHeight: 1.4,
                        marginTop: 2,
                      }}
                    >
                      {agent.task}
                    </div>
                  )}
                  {agent.status === "running" && (
                    <div
                      className="mt-1"
                      style={{
                        width: "100%",
                        height: 3,
                        backgroundColor: "#333",
                        overflow: "hidden",
                      }}
                    >
                      <motion.div
                        style={{
                          height: "100%",
                          backgroundColor: agent.accent,
                        }}
                        animate={{ width: ["0%", "100%"] }}
                        transition={{
                          repeat: Infinity,
                          duration: 2,
                          ease: "linear",
                        }}
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

interface AgentDialogProps {
  agent: Agent | null;
  onClose: () => void;
}

export function AgentDialog({ agent, onClose }: AgentDialogProps) {
  if (!agent) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center pb-8"
      onClick={onClose}
    >
      <motion.div
        className="relative"
        style={{
          width: 600,
          maxWidth: "90vw",
          backgroundColor: "#F5F5F0",
          border: "4px solid #333",
          borderRadius: 4,
          boxShadow: "4px 4px 0 #333",
          fontFamily: "'Press Start 2P', monospace",
        }}
        initial={{ y: 50, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 50, opacity: 0 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-4 py-2"
          style={{
            borderBottom: "3px solid #333",
            backgroundColor: agent.accent,
          }}
        >
          <span style={{ fontSize: 8, color: "#fff" }}>{agent.name}</span>
          <span
            style={{
              fontSize: 6,
              color: "#fff",
              opacity: 0.8,
              textTransform: "uppercase",
            }}
          >
            {agent.status}
          </span>
        </div>

        <div className="p-4">
          {/* Description */}
          <TypewriterText text={agent.description} />

          {/* Current task */}
          {agent.task && (
            <div className="mt-3" style={{ fontSize: 6, color: "#666" }}>
              <span style={{ color: "#333" }}>TASK: </span>
              {agent.task}
            </div>
          )}

          {/* Log */}
          <div className="mt-3" style={{ borderTop: "2px solid #ddd", paddingTop: 8 }}>
            <div style={{ fontSize: 6, color: "#999", marginBottom: 4 }}>LOG:</div>
            <div
              style={{
                maxHeight: 80,
                overflowY: "auto",
              }}
            >
              {agent.logs.map((log, i) => (
                <div
                  key={i}
                  style={{ fontSize: 5, color: "#666", marginBottom: 2, lineHeight: 1.5 }}
                >
                  {">"} {log}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Close hint */}
        <div
          className="text-center py-1"
          style={{
            fontSize: 5,
            color: "#999",
            borderTop: "2px solid #ddd",
          }}
        >
          CLICK ANYWHERE TO CLOSE
        </div>
      </motion.div>
    </div>
  );
}

function TypewriterText({ text }: { text: string }) {
  return (
    <motion.div
      style={{ fontSize: 7, color: "#333", lineHeight: 1.8 }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
    >
      {text.split("").map((char, i) => (
        <motion.span
          key={i}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: i * 0.025, duration: 0.01 }}
        >
          {char}
        </motion.span>
      ))}
    </motion.div>
  );
}

interface DealOverlayProps {
  deal: Deal;
  onClose: () => void;
}

export function DealOverlay({ deal, onClose }: DealOverlayProps) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center"
      style={{ backgroundColor: "rgba(0,0,0,0.7)" }}
      onClick={onClose}
    >
      <motion.div
        className="relative"
        style={{
          width: 500,
          maxWidth: "90vw",
          backgroundColor: "#2D5A3D",
          border: "6px solid #8B7355",
          borderRadius: 4,
          boxShadow: "6px 6px 0 rgba(0,0,0,0.4)",
          fontFamily: "'Press Start 2P', monospace",
          color: "#C8E6C9",
        }}
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6">
          <div style={{ fontSize: 10, marginBottom: 16 }}>{deal.leadName}</div>
          <div style={{ fontSize: 7, opacity: 0.8, marginBottom: 20 }}>
            {deal.serviceType}
          </div>

          <div className="flex items-center justify-between mb-4" style={{ gap: 12 }}>
            <div>
              <div style={{ fontSize: 6, opacity: 0.6, marginBottom: 4 }}>STATUS</div>
              <div style={{ fontSize: 8 }}>{deal.status}</div>
            </div>
            <div>
              <div style={{ fontSize: 6, opacity: 0.6, marginBottom: 4 }}>PHASE</div>
              <div style={{ fontSize: 8 }}>{ZONE_LABELS[deal.currentPhase]}</div>
            </div>
          </div>

          {/* Progress */}
          <div style={{ marginBottom: 16 }}>
            <div className="flex justify-between mb-1" style={{ fontSize: 6 }}>
              <span>PROGRESS</span>
              <span>{deal.progress}%</span>
            </div>
            <div
              style={{
                width: "100%",
                height: 12,
                backgroundColor: "rgba(0,0,0,0.3)",
                borderRadius: 2,
              }}
            >
              <motion.div
                style={{
                  height: "100%",
                  backgroundColor: "#A5D6A7",
                  borderRadius: 2,
                }}
                initial={{ width: 0 }}
                animate={{ width: `${deal.progress}%` }}
                transition={{ duration: 0.8 }}
              />
            </div>
          </div>

          {/* Gates */}
          <div style={{ fontSize: 7, marginBottom: 8 }}>GATES</div>
          <div className="flex flex-col" style={{ gap: 6 }}>
            {[
              { label: "G1 - Proposal Approved", value: deal.gates.proposal_approved },
              { label: "G2 - Kickoff Confirmed", value: deal.gates.kickoff_confirmed },
              { label: "G3 - Delivery Approved", value: deal.gates.delivery_approved },
            ].map((gate) => (
              <div key={gate.label} className="flex items-center" style={{ gap: 8 }}>
                <div
                  className="rounded-full"
                  style={{
                    width: 10,
                    height: 10,
                    backgroundColor: gate.value ? "#66BB6A" : "#EF5350",
                    boxShadow: `0 0 6px ${gate.value ? "#66BB6A" : "#EF5350"}`,
                  }}
                />
                <span style={{ fontSize: 6 }}>{gate.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div
          className="text-center py-2"
          style={{
            fontSize: 5,
            opacity: 0.6,
            borderTop: "2px solid rgba(255,255,255,0.1)",
          }}
        >
          CLICK OUTSIDE TO CLOSE
        </div>
      </motion.div>
    </div>
  );
}
