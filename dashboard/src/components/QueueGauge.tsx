"use client";

import { motion } from "framer-motion";
import { Users, AlertTriangle } from "lucide-react";

interface QueueGaugeProps {
    queueDepth: number;
    avgWait: number;
    abandonRate: number;
}

export default function QueueGauge({ queueDepth, avgWait, abandonRate }: QueueGaugeProps) {
    // Dynamic color based on queue depth
    const getStatusColor = (depth: number) => {
        if (depth <= 1) return "text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border-emerald-500/20";
        if (depth <= 3) return "text-amber-600 dark:text-amber-400 bg-amber-500/10 border-amber-500/20";
        return "text-rose-600 dark:text-rose-400 bg-rose-500/10 border-rose-500/20";
    };

    const getStatusLabel = (depth: number) => {
        if (depth === 0) return "Empty";
        if (depth <= 2) return "Normal Wait";
        if (depth <= 4) return "Crowded Billing";
        return "Critical Queue Depth";
    };

    const statusStyle = getStatusColor(queueDepth);
    const progress = Math.min((queueDepth / 6) * 100, 100);

    return (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100/50 dark:bg-zinc-900/40 p-6 backdrop-blur-md transition-colors duration-300">
            <h3 className="text-sm font-medium tracking-wider text-zinc-500 dark:text-zinc-400 uppercase">
                Billing Queue Status
            </h3>
            
            <div className="mt-6 flex items-center justify-between gap-6">
                {/* Radial Gauge representation */}
                <div className="relative flex h-24 w-24 items-center justify-center">
                    <svg className="h-full w-full -rotate-90 transform">
                        <circle
                            cx="48"
                            cy="48"
                            r="38"
                            className="stroke-zinc-200 dark:stroke-zinc-800"
                            strokeWidth="6"
                            fill="transparent"
                        />
                        <motion.circle
                            cx="48"
                            cy="48"
                            r="38"
                            className={
                                queueDepth <= 1 
                                    ? "stroke-emerald-500 dark:stroke-emerald-400" 
                                    : queueDepth <= 3 
                                        ? "stroke-amber-500 dark:stroke-amber-400" 
                                        : "stroke-rose-500 dark:stroke-rose-400"
                            }
                            strokeWidth="6"
                            fill="transparent"
                            strokeDasharray={2 * Math.PI * 38}
                            initial={{ strokeDashoffset: 2 * Math.PI * 38 }}
                            animate={{ strokeDashoffset: 2 * Math.PI * 38 * (1 - progress / 100) }}
                            transition={{ duration: 0.8, ease: "easeInOut" }}
                            strokeLinecap="round"
                        />
                    </svg>
                    <div className="absolute flex flex-col items-center justify-center text-center">
                        <span className="text-2xl font-bold tracking-tight text-zinc-950 dark:text-zinc-50">
                            {queueDepth}
                        </span>
                        <span className="text-[10px] font-medium text-zinc-500 dark:text-zinc-400 uppercase">
                            In Queue
                        </span>
                    </div>
                </div>

                {/* Queue Stats Detail */}
                <div className="flex flex-1 flex-col justify-center">
                    <div className={`inline-flex items-center w-fit gap-1.5 rounded-full border px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${statusStyle}`}>
                        <span className="h-1 w-1 rounded-full bg-current animate-ping" />
                        {getStatusLabel(queueDepth)}
                    </div>
                    
                    <div className="mt-4 grid grid-cols-2 gap-4">
                        <div>
                            <span className="text-[10px] text-zinc-500 block uppercase font-medium">
                                Avg Wait Time
                            </span>
                            <span className="text-md font-semibold text-zinc-800 dark:text-zinc-200">
                                {avgWait ? `${Math.round(avgWait)} seconds` : "0s"}
                            </span>
                        </div>
                        <div>
                            <span className="text-[10px] text-zinc-500 block uppercase font-medium">
                                Abandon Rate
                            </span>
                            <span className="text-md font-semibold text-zinc-800 dark:text-zinc-200">
                                {abandonRate ? `${Math.round(abandonRate * 100)}%` : "0%"}
                            </span>
                        </div>
                    </div>
                </div>
            </div>

            {queueDepth >= 4 && (
                <div className="mt-4 flex items-center gap-2 rounded-xl border border-rose-500/10 bg-rose-500/5 p-3 text-xs text-rose-600 dark:text-rose-400">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    <span>High congestion at checkouts. Dispatch extra store staff to tills.</span>
                </div>
            )}
        </div>
    );
}
