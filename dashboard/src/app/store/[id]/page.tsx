"use client";

import { use, useState, useEffect } from "react";
import { useZones } from "@/hooks/useZones";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useStoreMetrics } from "@/hooks/useStoreMetrics";
import StoreSelector from "@/components/StoreSelector";
import LiveCanvas from "@/components/LiveCanvas";
import MetricsCards from "@/components/MetricsCards";
import QueueGauge from "@/components/QueueGauge";
import ZoneHeatmap from "@/components/ZoneHeatmap";
import CCTVViewer from "@/components/CCTVViewer";
import { Video } from "lucide-react";

interface PageProps {
    params: Promise<{ id: string }>;
}

export default function StorePage({ params }: PageProps) {
    const { id } = use(params);
    const [activeTab, setActiveTab] = useState<"analytics" | "cctv">("analytics");
    
    // Check url search params to switch to CCTV tab if requested
    useEffect(() => {
        if (typeof window !== "undefined") {
            const searchParams = new URLSearchParams(window.location.search);
            const tabParam = searchParams.get("tab");
            if (tabParam === "cctv") {
                setActiveTab("cctv");
            }
        }
    }, []);

    // Store metadata lookup
    const storeInfoMap: Record<string, { name: string; layout: string }> = {
        ST1076: { name: "Mumbai Central", layout: "Store 1 - layout.png" },
        ST1008: { name: "Delhi SelectCitywalk", layout: "store 2 - layout.png" }
    };

    const storeInfo = storeInfoMap[id] || { name: "Unknown Store", layout: "Store 1 - layout.png" };

    // 1. Fetch static zone layout from API
    const { zones, loading: zonesLoading } = useZones(id);

    // 2. Fetch/Poll aggregate metrics from REST
    const { metrics, loading: metricsLoading } = useStoreMetrics(id);

    // 3. Connect to WebSocket live telemetry stream
    const { lastMessage, isConnected, isIdle } = useWebSocket(id);

    return (
        <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 text-zinc-900 dark:text-zinc-50 flex flex-col font-sans transition-colors duration-300">
            {/* Top Navigation */}
            <StoreSelector currentStoreId={id} />

            <main className="flex-1 mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8 space-y-6">
                {/* Store Header Status bar */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-zinc-200 dark:border-zinc-900 pb-5">
                    <div>
                        <div className="flex items-center gap-2">
                            <span className="text-2xl font-bold tracking-tight text-zinc-950 dark:text-zinc-100">{storeInfo.name}</span>
                            <span className="text-zinc-400 dark:text-zinc-500 text-sm font-semibold">({id})</span>
                        </div>
                        <p className="text-xs text-zinc-655 dark:text-zinc-400 mt-0.5">
                            Real-time camera tracks and spatial zone monitoring
                        </p>
                    </div>

                    <div className="flex items-center gap-3">
                        {/* WebSocket connection status indicator */}
                        <div className="flex items-center gap-2 border border-zinc-200 dark:border-zinc-800 bg-zinc-100 dark:bg-zinc-900/30 rounded-xl px-3 py-1.5 text-[10px] font-semibold tracking-wider uppercase">
                            <span className={`h-1.5 w-1.5 rounded-full ${
                                isConnected ? "bg-purple-500 animate-pulse" : "bg-zinc-400 dark:bg-zinc-600"
                            }`} />
                            <span className={isConnected ? "text-purple-600 dark:text-purple-400" : "text-zinc-400 dark:text-zinc-500"}>
                                {isConnected ? "Live Stream Connected" : "Connecting..."}
                            </span>
                        </div>
                    </div>
                </div>

                {/* Aggregate cards */}
                <MetricsCards metrics={metrics} loading={metricsLoading} />

                {/* Navigation Tabs */}
                <div className="flex items-center gap-2 border-b border-zinc-200 dark:border-zinc-900 pb-px">
                    <button
                        onClick={() => setActiveTab("analytics")}
                        className={`px-4 py-2.5 text-xs font-semibold uppercase tracking-wider border-b-2 transition-all ${
                            activeTab === "analytics"
                                ? "border-purple-500 text-purple-600 dark:text-purple-400 font-bold"
                                : "border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-300"
                        }`}
                    >
                        Live Analytics & Map
                    </button>
                    <button
                        onClick={() => setActiveTab("cctv")}
                        className={`px-4 py-2.5 text-xs font-semibold uppercase tracking-wider border-b-2 transition-all flex items-center gap-1.5 ${
                            activeTab === "cctv"
                                ? "border-purple-500 text-purple-600 dark:text-purple-400 font-bold"
                                : "border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-300"
                        }`}
                    >
                        <Video className="h-3.5 w-3.5" />
                        Store CCTV Feeds
                    </button>
                </div>

                {/* Main Split: Live Map Canvas & Side Metrics OR CCTV Video Feeds */}
                {activeTab === "analytics" ? (
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
                        {/* Left: Canvas Tracking */}
                        <div className="lg:col-span-2 space-y-4">
                            <div className="flex items-center justify-between">
                                <span className="text-xs font-semibold uppercase tracking-wider text-zinc-550 dark:text-zinc-400">
                                    Live Tracking Overlay
                                </span>
                                {isIdle && (
                                    <span className="text-[10px] uppercase font-bold text-amber-600 dark:text-amber-500 animate-pulse">
                                        Idle mode active
                                    </span>
                                )}
                            </div>
                            
                            <LiveCanvas
                                storeId={id}
                                layoutImage={storeInfo.layout}
                                zones={zones}
                                lastMessage={lastMessage}
                                isIdle={isIdle}
                            />
                        </div>

                        {/* Right: Sidebars */}
                        <div className="space-y-6">
                            <QueueGauge
                                queueDepth={metrics?.queue_depth ?? 0}
                                avgWait={metrics?.queue_wait_seconds_avg ?? 0}
                                abandonRate={metrics?.queue_abandon_rate ?? 0}
                            />

                            <ZoneHeatmap 
                                zonesBreakdown={metrics?.zone_breakdown ?? []} 
                            />
                        </div>
                    </div>
                ) : (
                    <CCTVViewer storeId={id} />
                )}
            </main>
        </div>
    );
}
