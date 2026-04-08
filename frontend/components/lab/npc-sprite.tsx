"use client";

import { motion } from "framer-motion";
import type { Agent } from "@/lib/lab-data";

interface NPCSpriteProps {
  agent: Agent;
  x: number;
  y: number;
  onClick: () => void;
}

export function NPCSprite({ agent, x, y, onClick }: NPCSpriteProps) {
  const isRunning = agent.status === "running";
  const isBlocked = agent.status === "blocked";
  const isCompleted = agent.status === "completed";

  return (
    <motion.div
      className="absolute cursor-pointer select-none"
      style={{ width: 64, height: 90 }}
      animate={{ x, y }}
      transition={{ type: "tween", duration: 0.8, ease: "easeInOut" }}
      onClick={onClick}
    >
      {/* Status indicator above head */}
      <div className="flex justify-center mb-0.5" style={{ height: 14 }}>
        {isRunning && (
          <motion.span
            className="text-[8px] leading-none"
            animate={{ opacity: [1, 0.4, 1] }}
            transition={{ repeat: Infinity, duration: 1 }}
          >
            {">>>"}
          </motion.span>
        )}
        {isBlocked && (
          <span className="text-[10px] leading-none">{"||"}</span>
        )}
        {isCompleted && (
          <motion.span
            className="text-[10px] leading-none"
            animate={{ scale: [1, 1.3, 1], opacity: [1, 0.6, 1] }}
            transition={{ repeat: 3, duration: 0.4 }}
          >
            {"*"}
          </motion.span>
        )}
      </div>

      {/* Sprite body */}
      <motion.div
        className="relative mx-auto"
        style={{
          width: 40,
          height: 52,
          imageRendering: "pixelated" as const,
          filter: isBlocked ? "saturate(0.3)" : "none",
        }}
        animate={
          isRunning
            ? { scale: [1, 1.08, 1] }
            : { y: [0, -2, 0] }
        }
        transition={{
          repeat: Infinity,
          duration: isRunning ? 0.8 : 2,
          ease: "easeInOut",
        }}
      >
        {/* Glow effect for running agents */}
        {isRunning && (
          <motion.div
            className="absolute -inset-1.5 rounded-sm"
            style={{ backgroundColor: agent.accent, opacity: 0.3 }}
            animate={{ opacity: [0.15, 0.4, 0.15] }}
            transition={{ repeat: Infinity, duration: 1 }}
          />
        )}

        {/* Head */}
        <div
          className="relative mx-auto rounded-sm"
          style={{
            width: 20,
            height: 18,
            backgroundColor: "#FFD5B5",
            border: "2px solid #333",
          }}
        >
          {/* Hair */}
          <div
            className="absolute -top-1 left-0 right-0 rounded-t-sm"
            style={{
              height: 6,
              backgroundColor: agent.accent,
              border: "2px solid #333",
              borderBottom: "none",
            }}
          />
          {/* Eyes */}
          <div className="absolute flex justify-between px-1" style={{ top: 8, left: 1, right: 1 }}>
            <div className="rounded-full" style={{ width: 3, height: 3, backgroundColor: "#333" }} />
            <div className="rounded-full" style={{ width: 3, height: 3, backgroundColor: "#333" }} />
          </div>
        </div>

        {/* Body (lab coat) */}
        <div
          className="relative mx-auto"
          style={{
            width: 28,
            height: 24,
            backgroundColor: "#F5F5F0",
            border: "2px solid #333",
            borderTop: "none",
            marginTop: -1,
          }}
        >
          {/* Coat accent stripe */}
          <div
            className="absolute left-1/2 -translate-x-1/2"
            style={{
              width: 4,
              height: "100%",
              backgroundColor: agent.accent,
              opacity: 0.6,
            }}
          />
        </div>

        {/* Legs */}
        <div className="flex justify-center" style={{ gap: 4, marginTop: -1 }}>
          <div style={{ width: 8, height: 8, backgroundColor: "#444", border: "1px solid #333" }} />
          <div style={{ width: 8, height: 8, backgroundColor: "#444", border: "1px solid #333" }} />
        </div>
      </motion.div>

      {/* Role badge */}
      <div
        className="mx-auto mt-0.5 flex items-center justify-center rounded-sm"
        style={{
          width: 20,
          height: 12,
          backgroundColor: agent.accent,
          border: "1px solid #333",
          fontSize: 5,
          color: "#fff",
          fontFamily: "monospace",
          fontWeight: "bold",
          letterSpacing: -0.5,
        }}
      >
        {agent.icon}
      </div>

      {/* Name label */}
      <div
        className="text-center mt-0.5 whitespace-nowrap"
        style={{
          fontSize: 6,
          fontFamily: "'Press Start 2P', monospace",
          color: "#333",
          textShadow: "0 0 2px rgba(255,255,255,0.8)",
        }}
      >
        {agent.name}
      </div>
    </motion.div>
  );
}
