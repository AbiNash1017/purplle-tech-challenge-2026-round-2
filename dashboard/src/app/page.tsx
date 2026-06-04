"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, ShoppingBag, Users, Clock, ShieldAlert, CreditCard, Video } from "lucide-react";
import StoreSelector from "@/components/StoreSelector";
import { useStoreMetrics, StoreMetrics } from "@/hooks/useStoreMetrics";
import { useTheme } from "@/context/ThemeContext";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

export default function Home() {
    const { theme } = useTheme();
    // Poll metrics for both stores
    const store1Data = useStoreMetrics("ST1076");
    const store2Data = useStoreMetrics("ST1008");

    const [chartData, setChartData] = useState<any[]>([]);

    // Generate mock historic hourly data for footfall trend representation
    useEffect(() => {
        const data = [];
        const hours = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00"];
        
        for (const h of hours) {
            data.push({
                hour: h,
                "Store 1 (Mumbai)": Math.floor(Math.random() * 20) + 10,
                "Store 2 (Delhi)": Math.floor(Math.random() * 25) + 15
            });
        }
        setChartData(data);
    }, []);

    const s1 = store1Data.metrics;
    const s2 = store2Data.metrics;

    // Aggregates
    const totalActive = (s1?.active_customers ?? 0) + (s2?.active_customers ?? 0);
    const totalFootfall = (s1?.footfall_count ?? 0) + (s2?.footfall_count ?? 0);
    const avgDwell = s1 && s2 
        ? Math.round((s1.avg_dwell_seconds + s2.avg_dwell_seconds) / 2) 
        : (s1?.avg_dwell_seconds || s2?.avg_dwell_seconds || 0);

    const formatDwell = (secs: number) => {
        if (!secs) return "0s";
        const mins = Math.floor(secs / 60);
        const s = Math.round(secs % 60);
        return mins > 0 ? `${mins}m ${s}s` : `${s}s`;
    };

    // Chart styling helpers
    const gridColor = theme === "light" ? "#e4e4e7" : "#27272a";
    const axisColor = theme === "light" ? "#71717a" : "#a1a1aa";
    const tooltipBg = theme === "light" ? "#ffffff" : "#09090b";
    const tooltipBorder = theme === "light" ? "#e4e4e7" : "#27272a";

    return (
        <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 text-zinc-900 dark:text-zinc-50 flex flex-col font-sans transition-colors duration-300">
            {/* Top Selector / Header */}
            <StoreSelector />

            <main className="flex-1 mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8 space-y-8">
                {/* Intro Hero Section */}
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-zinc-200 dark:border-zinc-900 pb-6">
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight bg-gradient-to-r from-zinc-800 to-zinc-500 dark:from-zinc-50 dark:via-zinc-200 dark:to-zinc-500 bg-clip-text text-transparent">
                            Retail Network Intelligence
                        </h1>
                        <p className="text-sm text-zinc-600 dark:text-zinc-400 mt-1">
                            Real-time camera analytics and POS correlation across active stores.
                        </p>
                    </div>
                    <div className="flex items-center gap-3 border border-zinc-200 dark:border-zinc-800 bg-zinc-100 dark:bg-zinc-900/30 rounded-xl px-4 py-2 text-xs">
                        <Users className="h-4 w-4 text-purple-600 dark:text-purple-400" />
                        <span>
                            <strong className="text-zinc-950 dark:text-zinc-100">{totalActive}</strong> active customers shopping
                        </span>
                    </div>
                </div>

                {/* Aggregate KPI grid */}
                <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100 dark:bg-zinc-900/30 p-5">
                        <span className="text-[10px] uppercase font-bold tracking-wider text-zinc-550">
                            Network Active Footfall
                        </span>
                        <div className="text-2xl font-bold mt-2 text-purple-600 dark:text-purple-400">{totalActive}</div>
                        <p className="text-[10px] text-zinc-500 mt-1">Live customer tracks</p>
                    </div>
                    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100 dark:bg-zinc-900/30 p-5">
                        <span className="text-[10px] uppercase font-bold tracking-wider text-zinc-550">
                            Cumulative Network Visits
                        </span>
                        <div className="text-2xl font-bold mt-2">{totalFootfall}</div>
                        <p className="text-[10px] text-zinc-500 mt-1">Today's total visitors</p>
                    </div>
                    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100 dark:bg-zinc-900/30 p-5">
                        <span className="text-[10px] uppercase font-bold tracking-wider text-zinc-550">
                            Average Session Dwell
                        </span>
                        <div className="text-2xl font-bold mt-2">{formatDwell(avgDwell)}</div>
                        <p className="text-[10px] text-zinc-500 mt-1">Mean duration in stores</p>
                    </div>
                    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100 dark:bg-zinc-900/30 p-5">
                        <span className="text-[10px] uppercase font-bold tracking-wider text-zinc-550">
                            Average Checkout Wait
                        </span>
                        <div className="text-2xl font-bold mt-2">
                            {Math.round(((s1?.queue_wait_seconds_avg || 0) + (s2?.queue_wait_seconds_avg || 0)) / 2)}s
                        </div>
                        <p className="text-[10px] text-zinc-500 mt-1">Queue completion wait</p>
                    </div>
                </div>

                {/* Stores Segment Cards */}
                <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
                    {/* Store 1 */}
                    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100/50 dark:bg-zinc-900/20 overflow-hidden flex flex-col justify-between hover:border-zinc-400 dark:hover:border-zinc-700 transition-colors">
                        <div className="p-6">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <ShoppingBag className="h-5 w-5 text-purple-600 dark:text-purple-400" />
                                    <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">Store 1</h2>
                                </div>
                                <span className="text-xs text-zinc-550 font-medium">Mumbai Central</span>
                            </div>
                            
                            <div className="mt-6 grid grid-cols-3 gap-4 border-y border-zinc-200 dark:border-zinc-900 py-4 my-4">
                                <div>
                                    <span className="text-[10px] text-zinc-500 uppercase block font-medium">Active</span>
                                    <span className="text-xl font-bold text-zinc-850 dark:text-zinc-200">{s1?.active_customers ?? 0}</span>
                                </div>
                                <div>
                                    <span className="text-[10px] text-zinc-500 uppercase block font-medium">Today's Visits</span>
                                    <span className="text-xl font-bold text-zinc-850 dark:text-zinc-200">{s1?.footfall_count ?? 0}</span>
                                </div>
                                <div>
                                    <span className="text-[10px] text-zinc-500 uppercase block font-medium">Queue Wait</span>
                                    <span className="text-xl font-bold text-zinc-850 dark:text-zinc-200">{s1?.queue_wait_seconds_avg ? `${Math.round(s1.queue_wait_seconds_avg)}s` : "0s"}</span>
                                </div>
                            </div>
                            
                            <div className="flex items-center justify-between text-xs text-zinc-550 dark:text-zinc-500">
                                <span className="flex items-center gap-1.5">
                                    <CreditCard className="h-3.5 w-3.5" />
                                    POS Correlation Rate: <strong>{s1?.pos_correlation_rate ? `${Math.round(s1.pos_correlation_rate * 100)}%` : "0%"}</strong>
                                </span>
                            </div>
                        </div>
                        <div className="bg-zinc-200/20 dark:bg-zinc-900/50 px-6 py-4 border-t border-zinc-200 dark:border-zinc-900 flex items-center justify-between">
                            <Link 
                                href="/store/ST1076?tab=cctv"
                                className="inline-flex items-center gap-1.5 text-xs font-semibold text-purple-650 dark:text-purple-400 hover:text-purple-800 dark:hover:text-purple-300 transition-colors"
                                title="Open CCTV footage player for Store 1 (Mumbai)"
                            >
                                <Video className="h-3.5 w-3.5" />
                                <span>View CCTV Footage</span>
                            </Link>
                            <Link 
                                href="/store/ST1076"
                                className="inline-flex items-center gap-1.5 text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:text-zinc-900 dark:hover:text-zinc-50 transition-colors"
                            >
                                <span>Open Live Analytics</span>
                                <ArrowRight className="h-3.5 w-3.5" />
                            </Link>
                        </div>
                    </div>

                    {/* Store 2 */}
                    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100/50 dark:bg-zinc-900/20 overflow-hidden flex flex-col justify-between hover:border-zinc-400 dark:hover:border-zinc-700 transition-colors">
                        <div className="p-6">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <ShoppingBag className="h-5 w-5 text-purple-600 dark:text-purple-400" />
                                    <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">Store 2</h2>
                                </div>
                                <span className="text-xs text-zinc-550 font-medium">Delhi SelectCitywalk</span>
                            </div>
                            
                            <div className="mt-6 grid grid-cols-3 gap-4 border-y border-zinc-200 dark:border-zinc-900 py-4 my-4">
                                <div>
                                    <span className="text-[10px] text-zinc-500 uppercase block font-medium">Active</span>
                                    <span className="text-xl font-bold text-zinc-850 dark:text-zinc-200">{s2?.active_customers ?? 0}</span>
                                </div>
                                <div>
                                    <span className="text-[10px] text-zinc-500 uppercase block font-medium">Today's Visits</span>
                                    <span className="text-xl font-bold text-zinc-850 dark:text-zinc-200">{s2?.footfall_count ?? 0}</span>
                                </div>
                                <div>
                                    <span className="text-[10px] text-zinc-500 uppercase block font-medium">Queue Wait</span>
                                    <span className="text-xl font-bold text-zinc-850 dark:text-zinc-200">{s2?.queue_wait_seconds_avg ? `${Math.round(s2.queue_wait_seconds_avg)}s` : "0s"}</span>
                                </div>
                            </div>
                            
                            <div className="flex items-center justify-between text-xs text-zinc-550 dark:text-zinc-500">
                                <span className="flex items-center gap-1.5">
                                    <CreditCard className="h-3.5 w-3.5" />
                                    POS Correlation Rate: <strong>{s2?.pos_correlation_rate ? `${Math.round(s2.pos_correlation_rate * 100)}%` : "0%"}</strong>
                                </span>
                            </div>
                        </div>
                        <div className="bg-zinc-200/20 dark:bg-zinc-900/50 px-6 py-4 border-t border-zinc-200 dark:border-zinc-900 flex items-center justify-between">
                            <Link 
                                href="/store/ST1008?tab=cctv"
                                className="inline-flex items-center gap-1.5 text-xs font-semibold text-purple-650 dark:text-purple-400 hover:text-purple-800 dark:hover:text-purple-300 transition-colors"
                                title="Open CCTV footage player for Store 2 (Delhi)"
                            >
                                <Video className="h-3.5 w-3.5" />
                                <span>View CCTV Footage</span>
                            </Link>
                            <Link 
                                href="/store/ST1008"
                                className="inline-flex items-center gap-1.5 text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:text-zinc-900 dark:hover:text-zinc-50 transition-colors"
                            >
                                <span>Open Live Analytics</span>
                                <ArrowRight className="h-3.5 w-3.5" />
                            </Link>
                        </div>
                    </div>
                </div>

                {/* Combined Footfall Trend Chart */}
                <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100/50 dark:bg-zinc-900/20 p-6">
                    <h3 className="text-sm font-medium tracking-wider text-zinc-500 dark:text-zinc-400 uppercase">
                        Footfall Distribution Trend
                    </h3>
                    <div className="h-72 w-full mt-6">
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={chartData}>
                                <defs>
                                    <linearGradient id="colorS1" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#a855f7" stopOpacity={0.2}/>
                                        <stop offset="95%" stopColor="#a855f7" stopOpacity={0}/>
                                    </linearGradient>
                                    <linearGradient id="colorS2" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.2}/>
                                        <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />
                                <XAxis dataKey="hour" stroke={axisColor} fontSize={10} tickLine={false} />
                                <YAxis stroke={axisColor} fontSize={10} tickLine={false} axisLine={false} />
                                <Tooltip 
                                    contentStyle={{ background: tooltipBg, borderColor: tooltipBorder, borderRadius: "12px" }}
                                    labelStyle={{ color: "#a1a1aa", fontSize: "11px", fontWeight: "bold" }}
                                />
                                <Area type="monotone" dataKey="Store 1 (Mumbai)" stroke="#a855f7" fillOpacity={1} fill="url(#colorS1)" />
                                <Area type="monotone" dataKey="Store 2 (Delhi)" stroke="#3b82f6" fillOpacity={1} fill="url(#colorS2)" />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </main>
        </div>
    );
}
