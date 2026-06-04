"use client";

import { useState, useEffect } from "react";
import { useCCTVCameras, CameraInfo } from "@/hooks/useCCTVCameras";
import { Play, Video, Eye, ShieldAlert, Sparkles } from "lucide-react";

interface CCTVViewerProps {
    storeId: string;
}

export default function CCTVViewer({ storeId }: CCTVViewerProps) {
    const { dataSource, loading, error } = useCCTVCameras(storeId);
    const [selectedCam, setSelectedCam] = useState<CameraInfo | null>(null);
    const [feedTime, setFeedTime] = useState("");

    // Reset selection whenever the store changes (prevents ST1076 cam bleeding into ST1008)
    useEffect(() => {
        setSelectedCam(null);
    }, [storeId]);

    // Set first camera as selected when data loads for this store
    useEffect(() => {
        if (dataSource?.cameras && dataSource.cameras.length > 0 && dataSource.store_id === storeId) {
            setSelectedCam(dataSource.cameras[0]);
        }
    }, [dataSource, storeId]);

    // Live updating timestamp for CCTV overlays
    useEffect(() => {
        const updateTimer = () => {
            const now = new Date();
            const dateStr = now.toLocaleDateString("en-GB", {
                day: "2-digit",
                month: "2-digit",
                year: "numeric",
            });
            const timeStr = now.toLocaleTimeString("en-GB", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
            });
            setFeedTime(`${dateStr} ${timeStr}`);
        };
        updateTimer();
        const timer = setInterval(updateTimer, 1000);
        return () => clearInterval(timer);
    }, []);

    if (loading) {
        return (
            <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100/50 dark:bg-zinc-900/20 p-8 flex flex-col items-center justify-center min-h-[350px] transition-all">
                <div className="h-8 w-8 rounded-full border-2 border-purple-500 border-t-transparent animate-spin mb-4" />
                <span className="text-xs text-zinc-550 dark:text-zinc-400 font-semibold tracking-wider uppercase animate-pulse">
                    Loading CCTV Camera Feeds...
                </span>
            </div>
        );
    }

    if (error || !dataSource || dataSource.cameras.length === 0) {
        return (
            <div className="rounded-2xl border border-red-500/20 dark:border-red-500/10 bg-red-500/5 p-8 flex flex-col items-center justify-center min-h-[300px] text-center">
                <ShieldAlert className="h-10 w-10 text-red-500 mb-4 animate-bounce" />
                <h3 className="text-sm font-semibold text-red-600 dark:text-red-400 uppercase tracking-wider">
                    CCTV Feeds Unavailable
                </h3>
                <p className="text-xs text-zinc-550 dark:text-zinc-500 mt-2 max-w-sm">
                    No pre-processed CCTV video feeds or summaries are available for store {storeId}. Serve simulated data or run the pipeline first to process camera outputs.
                </p>
            </div>
        );
    }

    const hostBase = "http://localhost:8000";

    return (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
            {/* Left/Middle: Live CCTV Feed Video Player */}
            <div className="lg:col-span-2 space-y-4">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <div className="h-2 w-2 rounded-full bg-red-600 animate-ping" />
                        <span className="text-xs font-semibold uppercase tracking-wider text-zinc-550 dark:text-zinc-400 flex items-center gap-1.5">
                            <Video className="h-4 w-4 text-zinc-650 dark:text-zinc-400" />
                            CCTV View: {selectedCam?.cam_id} ({selectedCam?.cam_role?.toUpperCase()} FEED)
                        </span>
                    </div>
                    <span className="text-[10px] uppercase font-bold tracking-wider text-purple-600 dark:text-purple-400 bg-purple-500/10 px-2 py-0.5 rounded-full flex items-center gap-1">
                        <Sparkles className="h-3 w-3" />
                        Annotated Output
                    </span>
                </div>

                {selectedCam ? (
                    <div className={`relative w-full overflow-hidden rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-zinc-950 flex items-center justify-center group ${storeId === "ST1008" ? "aspect-[8/9]" : "aspect-video"}`}>
                        {/* Video Element — keyed on full URL so React fully remounts when store or cam changes */}
                        <video
                            key={`${storeId}-${selectedCam.cam_id}`}
                            className="w-full h-full object-cover select-none"
                            controls
                            autoPlay
                            muted
                            loop
                            playsInline
                        >
                            <source src={`${hostBase}${selectedCam.video_path}`} type="video/mp4" />
                            Your browser does not support the video tag.
                        </video>

                        {/* Top Left: CCTV HUD Overlay */}
                        <div className="absolute top-4 left-4 pointer-events-none bg-black/60 backdrop-blur-sm rounded-lg px-3 py-2 text-[10px] text-zinc-300 font-mono space-y-0.5 border border-white/5 shadow-lg select-none">
                            <div className="flex items-center gap-1.5">
                                <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" />
                                <span className="font-bold text-white tracking-wider">REC SIM</span>
                            </div>
                            <div>CAM: {selectedCam.cam_id} ({selectedCam.cam_role.toUpperCase()})</div>
                            <div>LOC: STORE {storeId}</div>
                            <div>FPS: 25.00</div>
                            <div className="text-zinc-400">{feedTime}</div>
                        </div>

                    </div>
                ) : (
                    <div className={`w-full rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-zinc-900 flex flex-col items-center justify-center text-center ${storeId === "ST1008" ? "aspect-[8/9]" : "aspect-video"}`}>
                        <Play className="h-10 w-10 text-zinc-600 mb-2" />
                        <span className="text-xs text-zinc-550 dark:text-zinc-500">Select a camera to start feed</span>
                    </div>
                )}
            </div>

            {/* Right: Camera Selection & Stats Panel */}
            <div className="space-y-4">
                <span className="text-xs font-semibold uppercase tracking-wider text-zinc-550 dark:text-zinc-400 block">
                    Available Cameras ({dataSource.cameras.length})
                </span>

                <div className="space-y-3">
                    {dataSource.cameras.map((cam) => {
                        const isSelected = selectedCam?.cam_id === cam.cam_id;
                        return (
                            <button
                                key={cam.cam_id}
                                onClick={() => setSelectedCam(cam)}
                                className={`w-full text-left rounded-xl border p-4 transition-all flex flex-col gap-2 ${
                                    isSelected
                                        ? "border-purple-500 bg-purple-500/5 dark:bg-purple-500/10 shadow-sm"
                                        : "border-zinc-200 dark:border-zinc-800 bg-zinc-100/50 dark:bg-zinc-900/20 hover:border-zinc-300 dark:hover:border-zinc-700 hover:bg-zinc-200/30 dark:hover:bg-zinc-800/30"
                                }`}
                            >
                                <div className="flex items-center justify-between w-full">
                                    <div className="flex items-center gap-2">
                                        <span className={`h-1.5 w-1.5 rounded-full ${
                                            isSelected ? "bg-purple-500 animate-pulse" : "bg-zinc-400 dark:bg-zinc-600"
                                        }`} />
                                        <span className="font-semibold text-sm text-zinc-900 dark:text-zinc-100">
                                            {cam.cam_id}
                                        </span>
                                    </div>
                                    <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded-full bg-zinc-200/60 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400">
                                        {cam.cam_role}
                                    </span>
                                </div>

                                <div className="grid grid-cols-3 gap-2 border-t border-zinc-200/50 dark:border-zinc-800/80 pt-2 text-[10px] text-zinc-500">
                                    <div>
                                        <span className="block text-zinc-450 dark:text-zinc-500 uppercase font-medium">Events</span>
                                        <span className="font-bold text-zinc-700 dark:text-zinc-300">{cam.total_events}</span>
                                    </div>
                                    <div>
                                        <span className="block text-zinc-450 dark:text-zinc-500 uppercase font-medium">Cust.</span>
                                        <span className="font-bold text-zinc-700 dark:text-zinc-300">{cam.customers}</span>
                                    </div>
                                    <div>
                                        <span className="block text-zinc-450 dark:text-zinc-500 uppercase font-medium">Staff</span>
                                        <span className="font-bold text-zinc-700 dark:text-zinc-300">{cam.staff}</span>
                                    </div>
                                </div>

                                <div className="flex items-center gap-1 text-[10px] text-purple-600 dark:text-purple-400 font-semibold mt-1">
                                    <Eye className="h-3.5 w-3.5" />
                                    <span>{isSelected ? "Currently Viewing Feed" : "Click to view video"}</span>
                                </div>
                            </button>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}
