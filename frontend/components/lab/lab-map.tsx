"use client";

import { type RefObject } from "react";
import { motion } from "framer-motion";
import { NPCSprite } from "./npc-sprite";
import type { Agent, Deal } from "@/lib/lab-data";
import { BLACKBOARD_POS } from "@/lib/lab-data";

// Posizioni legacy — mantenute per retrocompatibilità (componente non più montato)
const AGENT_POSITIONS: Record<string, { x: number; y: number }> = {
  scout:            { x: 12, y: 20 },
  lead_profiler:    { x: 25, y: 20 },
  analyst:          { x: 38, y: 20 },
  design:           { x: 58, y: 20 },
  proposal:         { x: 72, y: 20 },
  sales:            { x: 86, y: 20 },
  delivery_orchestrator: { x: 12, y: 68 },
  doc_generator:    { x: 25, y: 68 },
  delivery_tracker: { x: 38, y: 68 },
  account_manager:  { x: 58, y: 68 },
  billing:          { x: 72, y: 68 },
  support:          { x: 86, y: 68 },
};

interface LabMapProps {
  agents: Agent[];
  deal: Deal;
  onAgentClick: (agent: Agent) => void;
  onBlackboardClick: () => void;
  mapRef: RefObject<HTMLDivElement | null>;
}

export function LabMap({ agents, deal, onAgentClick, onBlackboardClick, mapRef }: LabMapProps) {
  const getAgentPosition = (agent: Agent) => {
    const homePos = AGENT_POSITIONS[agent.id];
    if (agent.status === "running") {
      return {
        x: homePos.x + (BLACKBOARD_POS.x - homePos.x) * 0.25,
        y: homePos.y + (BLACKBOARD_POS.y - homePos.y) * 0.25,
      };
    }
    return homePos;
  };

  return (
    <div
      ref={mapRef}
      style={{ position: "relative", width: "100vw", height: "100vh", overflow: "hidden" }}
    >
      {/* Layer 0 — Background image */}
      <img
        src="/lab-bg.png"
        alt="Lab"
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          objectFit: "contain",
          objectPosition: "center",
          imageRendering: "pixelated" as const,
          display: "block",
        }}
      />

      {/* Layer 1 — Interactive overlay */}
      <div style={{ position: "absolute", inset: 0 }}>

        {/* Blackboard clickable hotspot */}
        <div
          className="absolute cursor-pointer"
          style={{
            left: `${BLACKBOARD_POS.x}%`,
            top: `${BLACKBOARD_POS.y}%`,
            transform: "translate(-50%, -50%)",
            width: 200,
            height: 120,
            backgroundColor: "rgba(45,90,61,0.75)",
            border: "6px solid #8B7355",
            borderRadius: 3,
            boxShadow: "inset 0 0 20px rgba(0,0,0,0.3), 0 4px 0 #5C4033",
          }}
          onClick={onBlackboardClick}
        >
          <div className="p-2" style={{ fontFamily: "'Press Start 2P', monospace", color: "#C8E6C9" }}>
            <div style={{ fontSize: 6, marginBottom: 4 }}>{deal.leadName}</div>
            <div style={{ fontSize: 5, opacity: 0.8, marginBottom: 6 }}>{deal.serviceType}</div>
            <div style={{ fontSize: 5, marginBottom: 4 }}>STATUS: {deal.status}</div>
            {/* Progress bar */}
            <div style={{ width: "100%", height: 8, backgroundColor: "rgba(0,0,0,0.3)", borderRadius: 1 }}>
              <div
                style={{
                  width: `${deal.progress}%`,
                  height: "100%",
                  backgroundColor: "#A5D6A7",
                  borderRadius: 1,
                  transition: "width 0.5s ease",
                }}
              />
            </div>
            <div className="flex justify-between mt-1" style={{ fontSize: 4 }}>
              <span>{deal.progress}%</span>
              <div className="flex" style={{ gap: 4 }}>
                <span style={{ color: deal.gates.proposal_approved ? "#66BB6A" : "#EF5350" }}>G1</span>
                <span style={{ color: deal.gates.kickoff_confirmed ? "#66BB6A" : "#EF5350" }}>G2</span>
                <span style={{ color: deal.gates.delivery_approved ? "#66BB6A" : "#EF5350" }}>G3</span>
              </div>
            </div>
          </div>
          {/* Chalk tray */}
          <div
            className="absolute -bottom-3 left-2 right-2"
            style={{
              height: 4,
              backgroundColor: "#8B7355",
              borderRadius: 1,
            }}
          />
        </div>

        {/* NPC Sprites */}
        {agents.map((agent) => {
          const pos = getAgentPosition(agent);
          return (
            <motion.div
              key={agent.id}
              className="absolute"
              style={{ marginLeft: -32, marginTop: -45 }}
              animate={{
                left: `${pos.x}%`,
                top: `${pos.y}%`,
              }}
              transition={{ type: "tween", duration: 0.8, ease: "easeInOut" }}
            >
              <NPCSprite
                agent={agent}
                x={0}
                y={0}
                onClick={() => onAgentClick(agent)}
              />
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}
