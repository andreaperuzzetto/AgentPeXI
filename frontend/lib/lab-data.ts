export type AgentStatus = "idle" | "running" | "completed" | "blocked" | "pending";

export type Zone = "discovery" | "proposal" | "delivery" | "post_sale";

export interface Agent {
  id: string;
  name: string;
  icon: string;
  status: AgentStatus;
  zone: Zone;
  task: string | null;
  accent: string;
  description: string;
  logs: string[];
}

export interface Deal {
  id: string;
  leadName: string;
  serviceType: string;
  status: string;
  currentPhase: Zone;
  gates: {
    proposal_approved: boolean;
    kickoff_confirmed: boolean;
    delivery_approved: boolean;
  };
  progress: number;
}

export const ZONE_LABELS: Record<Zone, string> = {
  discovery: "Discovery",
  proposal: "Proposal",
  delivery: "Delivery",
  post_sale: "Post-Sale",
};

export const ZONE_ICONS: Record<Zone, string> = {
  discovery: "DISC",
  proposal: "PROP",
  delivery: "DLVR",
  post_sale: "POST",
};

export const initialAgents: Agent[] = [
  {
    id: "scout",
    name: "Scout",
    icon: "SC",
    status: "idle",
    zone: "discovery",
    task: null,
    accent: "#228B22",
    description: "Scans territory for new business opportunities and leads.",
    logs: ["System boot complete", "Territory map loaded", "Ready for scanning"],
  },
  {
    id: "lead_profiler",
    name: "Lead Profiler",
    icon: "LP",
    status: "idle",
    zone: "discovery",
    task: null,
    accent: "#0047AB",
    description: "Deep-profiles identified leads with company data enrichment.",
    logs: ["Profile database synced", "Enrichment APIs connected"],
  },
  {
    id: "analyst",
    name: "Analyst",
    icon: "AN",
    status: "idle",
    zone: "discovery",
    task: null,
    accent: "#FF8C00",
    description: "Scores and ranks leads by fit, intent, and potential value.",
    logs: ["Scoring model loaded", "Thresholds calibrated"],
  },
  {
    id: "design",
    name: "Design",
    icon: "DS",
    status: "idle",
    zone: "proposal",
    task: null,
    accent: "#FF1493",
    description: "Creates visual mockups and design proposals for clients.",
    logs: ["Design templates loaded", "Asset library synced"],
  },
  {
    id: "proposal",
    name: "Proposal",
    icon: "PR",
    status: "idle",
    zone: "proposal",
    task: null,
    accent: "#DAA520",
    description: "Assembles and formats commercial proposals with pricing.",
    logs: ["Proposal engine ready", "Templates available"],
  },
  {
    id: "sales",
    name: "Sales",
    icon: "SL",
    status: "idle",
    zone: "proposal",
    task: null,
    accent: "#DC143C",
    description: "Manages outreach, follow-ups, and deal negotiation.",
    logs: ["Email sequences loaded", "CRM connected"],
  },
  {
    id: "delivery_orchestrator",
    name: "Orchestrator",
    icon: "DO",
    status: "idle",
    zone: "delivery",
    task: null,
    accent: "#008080",
    description: "Coordinates all delivery activities and milestone tracking.",
    logs: ["Project plan template ready", "Team channels open"],
  },
  {
    id: "doc_generator",
    name: "Doc Gen",
    icon: "DG",
    status: "idle",
    zone: "delivery",
    task: null,
    accent: "#8B4513",
    description: "Auto-generates project documentation and deliverables.",
    logs: ["Doc templates loaded", "Export formats configured"],
  },
  {
    id: "delivery_tracker",
    name: "Tracker",
    icon: "DT",
    status: "idle",
    zone: "delivery",
    task: null,
    accent: "#32CD32",
    description: "Monitors delivery progress and quality checkpoints.",
    logs: ["Tracking dashboard initialized", "KPIs set"],
  },
  {
    id: "account_manager",
    name: "Account Mgr",
    icon: "AM",
    status: "idle",
    zone: "post_sale",
    task: null,
    accent: "#4B0082",
    description: "Manages ongoing client relationship and satisfaction.",
    logs: ["Client profiles loaded", "NPS survey ready"],
  },
  {
    id: "billing",
    name: "Billing",
    icon: "BL",
    status: "idle",
    zone: "post_sale",
    task: null,
    accent: "#708090",
    description: "Handles invoicing, payments, and financial reconciliation.",
    logs: ["Billing system synced", "Payment gateway connected"],
  },
  {
    id: "support",
    name: "Support",
    icon: "SP",
    status: "idle",
    zone: "post_sale",
    task: null,
    accent: "#00CED1",
    description: "Provides post-sale technical support and issue resolution.",
    logs: ["Ticket system ready", "Knowledge base indexed"],
  },
];

export const initialDeal: Deal = {
  id: "deal-001",
  leadName: "Pizzeria Da Mario",
  serviceType: "Web Design",
  status: "LEAD_IDENTIFIED",
  currentPhase: "discovery",
  gates: {
    proposal_approved: false,
    kickoff_confirmed: false,
    delivery_approved: false,
  },
  progress: 0,
};

export interface PipelineStep {
  agentIds: string[];
  tasks: Record<string, string>;
  logs: Record<string, string>;
  dealStatus: string;
  dealPhase: Zone;
  dealProgress: number;
  gates: { proposal_approved: boolean; kickoff_confirmed: boolean; delivery_approved: boolean };
}

export const pipelineSteps: PipelineStep[] = [
  {
    agentIds: ["scout"],
    tasks: { scout: "Scanning pizzerie in Milano zona Navigli" },
    logs: { scout: "Found 12 potential leads in target area" },
    dealStatus: "SCANNING",
    dealPhase: "discovery",
    dealProgress: 8,
    gates: { proposal_approved: false, kickoff_confirmed: false, delivery_approved: false },
  },
  {
    agentIds: ["lead_profiler"],
    tasks: { lead_profiler: "Profiling Pizzeria Da Mario - enriching data" },
    logs: { lead_profiler: "Company data enriched: revenue, employees, web presence" },
    dealStatus: "LEAD_PROFILED",
    dealPhase: "discovery",
    dealProgress: 20,
    gates: { proposal_approved: false, kickoff_confirmed: false, delivery_approved: false },
  },
  {
    agentIds: ["analyst"],
    tasks: { analyst: "Scoring lead - fit: 85, intent: 72, value: 90" },
    logs: { analyst: "Lead scored: A-tier, recommended for proposal" },
    dealStatus: "LEAD_QUALIFIED",
    dealPhase: "discovery",
    dealProgress: 30,
    gates: { proposal_approved: false, kickoff_confirmed: false, delivery_approved: false },
  },
  {
    agentIds: ["design", "proposal"],
    tasks: {
      design: "Creating mockup for pizzeria website",
      proposal: "Assembling commercial proposal EUR 4,500",
    },
    logs: {
      design: "Homepage mockup v1 complete with menu integration",
      proposal: "Proposal doc generated with 3 pricing tiers",
    },
    dealStatus: "PROPOSAL_DRAFTING",
    dealPhase: "proposal",
    dealProgress: 45,
    gates: { proposal_approved: false, kickoff_confirmed: false, delivery_approved: false },
  },
  {
    agentIds: ["sales"],
    tasks: { sales: "Sending proposal to Mario Rossi via email" },
    logs: { sales: "Proposal sent, follow-up scheduled in 48h" },
    dealStatus: "PROPOSAL_SENT",
    dealPhase: "proposal",
    dealProgress: 55,
    gates: { proposal_approved: true, kickoff_confirmed: false, delivery_approved: false },
  },
  {
    agentIds: ["delivery_orchestrator"],
    tasks: { delivery_orchestrator: "Setting up project plan and milestones" },
    logs: { delivery_orchestrator: "Project plan created: 4 sprints, 8 weeks" },
    dealStatus: "KICKOFF",
    dealPhase: "delivery",
    dealProgress: 65,
    gates: { proposal_approved: true, kickoff_confirmed: true, delivery_approved: false },
  },
  {
    agentIds: ["doc_generator", "delivery_tracker"],
    tasks: {
      doc_generator: "Generating project documentation pack",
      delivery_tracker: "Monitoring sprint 1 progress - 3/5 tasks done",
    },
    logs: {
      doc_generator: "Technical spec and user guide drafted",
      delivery_tracker: "Sprint 1: 60% complete, on schedule",
    },
    dealStatus: "IN_DELIVERY",
    dealPhase: "delivery",
    dealProgress: 78,
    gates: { proposal_approved: true, kickoff_confirmed: true, delivery_approved: false },
  },
  {
    agentIds: ["account_manager"],
    tasks: { account_manager: "Conducting NPS survey with client" },
    logs: { account_manager: "NPS score: 9/10, client very satisfied" },
    dealStatus: "POST_DELIVERY",
    dealPhase: "post_sale",
    dealProgress: 88,
    gates: { proposal_approved: true, kickoff_confirmed: true, delivery_approved: true },
  },
  {
    agentIds: ["billing", "support"],
    tasks: {
      billing: "Generating final invoice EUR 4,500",
      support: "Setting up maintenance ticket channel",
    },
    logs: {
      billing: "Invoice #2024-0847 sent, payment pending",
      support: "Support channel active, SLA configured",
    },
    dealStatus: "COMPLETED",
    dealPhase: "post_sale",
    dealProgress: 100,
    gates: { proposal_approved: true, kickoff_confirmed: true, delivery_approved: true },
  },
];

// Agent positions in the lab (percentage coordinates, 0–100 on both axes)
export const AGENT_POSITIONS: Record<string, { x: number; y: number }> = {
  // Discovery zone — top left
  scout:            { x: 12, y: 20 },
  lead_profiler:    { x: 25, y: 20 },
  analyst:          { x: 38, y: 20 },
  // Proposal zone — top right
  design:           { x: 58, y: 20 },
  proposal:         { x: 72, y: 20 },
  sales:            { x: 86, y: 20 },
  // Delivery zone — bottom left
  delivery_orchestrator: { x: 12, y: 68 },
  doc_generator:    { x: 25, y: 68 },
  delivery_tracker: { x: 38, y: 68 },
  // Post-sale zone — bottom right
  account_manager:  { x: 58, y: 68 },
  billing:          { x: 72, y: 68 },
  support:          { x: 86, y: 68 },
};

// Posizione della lavagna verde nell'immagine (lato destro, centro verticale)
export const BLACKBOARD_POS = { x: 62, y: 42 };
