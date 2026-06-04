"use client";

import { motion } from "framer-motion";
import { Users, Clock, Flame, CreditCard } from "lucide-react";
import { StoreMetrics } from "@/hooks/useStoreMetrics";

interface MetricsCardsProps {
    metrics: StoreMetrics | null;
    loading: boolean;
}

export default function MetricsCards({ metrics, loading }: MetricsCardsProps) {
    // Helper to format average dwell times (seconds to min/sec)
    const formatDwellTime = (seconds: number) => {
        if (!seconds) return "0s";
        const mins = Math.floor(seconds / 60);
        const secs = Math.round(seconds % 60);
        return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    };

    const cards = [
        {
            title: "Active Customers",
            value: metrics?.active_customers ?? 0,
            icon: Users,
            desc: "Customers currently inside",
            color: "border-purple-200 dark:border-purple-500/20 text-purple-600 dark:text-purple-400"
        },
        {
            title: "Total Footfall",
            value: metrics?.footfall_count ?? 0,
            icon: Flame,
            desc: "Unique visitor tracks",
            color: "border-zinc-200 dark:border-zinc-800 text-zinc-800 dark:text-zinc-300"
        },
        {
            title: "Avg Dwell Time",
            value: formatDwellTime(metrics?.avg_dwell_seconds ?? 0),
            icon: Clock,
            desc: "Average time in store",
            color: "border-zinc-200 dark:border-zinc-800 text-zinc-800 dark:text-zinc-300"
        },
        {
            title: "Queue Wait Time",
            value: metrics?.queue_wait_seconds_avg ? `${Math.round(metrics.queue_wait_seconds_avg)}s` : "0s",
            icon: Clock,
            desc: "Avg billing wait time",
            color: "border-zinc-200 dark:border-zinc-800 text-zinc-800 dark:text-zinc-300"
        },
        {
            title: "POS Correlation",
            value: metrics?.pos_correlation_rate ? `${Math.round(metrics.pos_correlation_rate * 100)}%` : "0%",
            icon: CreditCard,
            desc: "Exits linked to purchase",
            color: "border-zinc-200 dark:border-zinc-800 text-zinc-800 dark:text-zinc-300"
        }
    ];

    const container = {
        hidden: { opacity: 0 },
        show: {
            opacity: 1,
            transition: { staggerChildren: 0.08 }
        }
    } as const;

    const item = {
        hidden: { opacity: 0, y: 15 },
        show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 100 } }
    } as const;

    return (
        <motion.div 
            variants={container}
            initial="hidden"
            animate="show"
            className="grid grid-cols-2 gap-4 md:grid-cols-5"
        >
            {cards.map((card, idx) => {
                const Icon = card.icon;
                return (
                    <motion.div
                        key={idx}
                        variants={item}
                        className={`relative overflow-hidden rounded-2xl border bg-zinc-100/50 dark:bg-zinc-900/40 p-5 backdrop-blur-md transition-all hover:bg-zinc-200/50 dark:hover:bg-zinc-900/60 ${
                            loading ? "animate-pulse" : ""
                        } ${card.color}`}
                    >
                        <div className="flex items-center justify-between">
                            <span className="text-[10px] sm:text-xs font-medium text-zinc-500 dark:text-zinc-400 tracking-wider uppercase">
                                {card.title}
                            </span>
                            <Icon className="h-4 w-4 opacity-70" />
                        </div>
                        
                        <div className="mt-4 flex items-baseline gap-1.5">
                            <span className="text-xl sm:text-2xl font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
                                {loading ? "..." : card.value}
                            </span>
                        </div>
                        
                        <p className="mt-1 text-[10px] text-zinc-550 dark:text-zinc-500">
                            {card.desc}
                        </p>
                        
                        {/* Decorative subtle gradient background glow */}
                        <div className="absolute -right-6 -bottom-6 -z-10 h-16 w-16 rounded-full bg-zinc-800/10 blur-xl" />
                    </motion.div>
                );
            })}
        </motion.div>
    );
}
