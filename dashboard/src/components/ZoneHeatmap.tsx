"use client";

import { ZoneHeatmapPoint } from "@/hooks/useStoreMetrics";

interface ZoneHeatmapProps {
    zonesBreakdown: ZoneHeatmapPoint[];
}

export default function ZoneHeatmap({ zonesBreakdown }: ZoneHeatmapProps) {
    // Sort zones by dwell time descending
    const sortedZones = [...zonesBreakdown].sort((a, b) => b.dwell_seconds - a.dwell_seconds);
    const maxDwell = sortedZones.length > 0 ? Math.max(...sortedZones.map(z => z.dwell_seconds), 1) : 1;

    // Helper to get color by zone type
    const getZoneColorClass = (type: string) => {
        switch (type.toUpperCase()) {
            case "SHELF":
                return "bg-purple-500";
            case "DISPLAY":
                return "bg-blue-500 dark:bg-blue-400";
            case "BILLING":
                return "bg-amber-500 dark:bg-amber-400";
            case "ENTRANCE":
                return "bg-cyan-500 dark:bg-cyan-400";
            default:
                return "bg-zinc-400";
        }
    };

    const getZoneTextClass = (type: string) => {
        switch (type.toUpperCase()) {
            case "SHELF":
                return "text-purple-600 dark:text-purple-400 border-purple-300 dark:border-purple-500/20 bg-purple-500/5";
            case "DISPLAY":
                return "text-blue-600 dark:text-blue-400 border-blue-300 dark:border-blue-500/20 bg-blue-500/5";
            case "BILLING":
                return "text-amber-600 dark:text-amber-400 border-amber-300 dark:border-amber-500/20 bg-amber-500/5";
            case "ENTRANCE":
                return "text-cyan-600 dark:text-cyan-400 border-cyan-300 dark:border-cyan-500/20 bg-cyan-500/5";
            default:
                return "text-zinc-600 dark:text-zinc-400 border-zinc-300 dark:border-zinc-700 bg-zinc-200/50 dark:bg-zinc-800/10";
        }
    };

    const formatDwell = (seconds: number) => {
        if (seconds < 60) return `${Math.round(seconds)}s`;
        const mins = Math.floor(seconds / 60);
        return `${mins}m`;
    };

    return (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100/50 dark:bg-zinc-900/40 p-6 backdrop-blur-md transition-colors duration-300">
            <h3 className="text-sm font-medium tracking-wider text-zinc-500 dark:text-zinc-400 uppercase">
                Zone Heatmap Breakdown
            </h3>
            
            <div className="mt-6 space-y-4 max-h-[350px] overflow-y-auto pr-1 custom-scrollbar">
                {sortedZones.length === 0 ? (
                    <div className="text-center py-8 text-xs text-zinc-500">
                        No activity recorded yet
                    </div>
                ) : (
                    sortedZones.map((zone) => {
                        const progressPercent = (zone.dwell_seconds / maxDwell) * 100;
                        const barColor = getZoneColorClass(zone.zone_type);
                        const badgeStyle = getZoneTextClass(zone.zone_type);
                        
                        return (
                            <div key={zone.zone_id} className="space-y-1.5">
                                <div className="flex items-center justify-between text-xs">
                                    <div className="flex items-center gap-2">
                                        <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold border uppercase tracking-wide ${badgeStyle}`}>
                                            {zone.zone_type}
                                        </span>
                                        <span className="font-medium text-zinc-800 dark:text-zinc-200">
                                            {zone.zone_name}
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-3 text-zinc-500 dark:text-zinc-400">
                                        <span>{zone.visit_count} visits</span>
                                        <span className="font-semibold text-zinc-950 dark:text-zinc-50">{formatDwell(zone.dwell_seconds)}</span>
                                    </div>
                                </div>
                                
                                <div className="relative h-2 w-full rounded-full bg-zinc-200 dark:bg-zinc-800 overflow-hidden">
                                    <div
                                        className={`h-full rounded-full transition-all duration-500 ${barColor}`}
                                        style={{ width: `${progressPercent}%` }}
                                    />
                                </div>
                            </div>
                        );
                    })
                )}
            </div>
        </div>
    );
}
