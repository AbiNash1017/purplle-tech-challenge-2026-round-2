"use client";

import { useEffect, useRef, useState } from "react";
import { ZoneConfig } from "@/hooks/useZones";
import { WebSocketMessage } from "@/hooks/useWebSocket";
import { useTheme } from "@/context/ThemeContext";

interface LiveCanvasProps {
    storeId: string;
    layoutImage: string;
    zones: ZoneConfig[];
    lastMessage: WebSocketMessage | null;
    isIdle: boolean;
}

interface TrackPoint {
    id: string;
    currentX: number;
    currentY: number;
    targetX: number;
    targetY: number;
    lastUpdated: number;
    groupId: string | null;
    groupSize: number | null;
}

export default function LiveCanvas({ storeId, layoutImage, zones, lastMessage, isIdle }: LiveCanvasProps) {
    const { theme } = useTheme();
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const containerRef = useRef<HTMLDivElement | null>(null);
    const imgRef = useRef<HTMLImageElement | null>(null);
    
    const [imageLoaded, setImageLoaded] = useState(false);
    const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
    
    // Dictionary of active tracks: track_id -> TrackPoint
    const tracksRef = useRef<Record<string, TrackPoint>>({});

    // 1. Handle Resize/Alignment of Canvas to Layout Image
    useEffect(() => {
        const handleResize = () => {
            if (imgRef.current && canvasRef.current) {
                const rect = imgRef.current.getBoundingClientRect();
                setCanvasSize({ width: rect.width, height: rect.height });
            }
        };

        window.addEventListener("resize", handleResize);
        return () => window.removeEventListener("resize", handleResize);
    }, [imageLoaded]);

    // Update canvas dimensions when size state changes
    useEffect(() => {
        if (canvasRef.current) {
            canvasRef.current.width = canvasSize.width;
            canvasRef.current.height = canvasSize.height;
        }
    }, [canvasSize]);

    // Trigger alignment once image loads
    const handleImageLoad = () => {
        setImageLoaded(true);
        if (imgRef.current) {
            const rect = imgRef.current.getBoundingClientRect();
            setCanvasSize({ width: rect.width, height: rect.height });
        }
    };

    // 2. Consume Incoming WebSocket Messages
    useEffect(() => {
        if (!lastMessage || isIdle) return;
        
        const { event_type, track_id, location, group_id, group_size } = lastMessage;
        
        if (!track_id || track_id === "HEARTBEAT") return;

        const now = Date.now();
        
        // Handle Exit Event (remove track)
        if (event_type === "exit") {
            delete tracksRef.current[track_id];
            return;
        }

        // Parse coordinates
        if (location && location.coordinates) {
            const [xNorm, yNorm] = location.coordinates;
            
            const existing = tracksRef.current[track_id];
            if (existing) {
                // Update target coordinate
                tracksRef.current[track_id] = {
                    ...existing,
                    targetX: xNorm,
                    targetY: yNorm,
                    lastUpdated: now,
                    groupId: group_id || existing.groupId,
                    groupSize: group_size || existing.groupSize
                };
            } else {
                // Spawn new track
                tracksRef.current[track_id] = {
                    id: track_id,
                    currentX: xNorm,
                    currentY: yNorm,
                    targetX: xNorm,
                    targetY: yNorm,
                    lastUpdated: now,
                    groupId: group_id,
                    groupSize: group_size
                };
            }
        }
    }, [lastMessage, isIdle]);

    // 3. Animation & Rendering Loop
    useEffect(() => {
        let animationFrameId: number;
        
        const render = () => {
            const canvas = canvasRef.current;
            if (!canvas) {
                animationFrameId = requestAnimationFrame(render);
                return;
            }
            
            const ctx = canvas.getContext("2d");
            if (!ctx) {
                animationFrameId = requestAnimationFrame(render);
                return;
            }

            const { width, height } = canvas;
            ctx.clearRect(0, 0, width, height);

            // Clean up old/expired tracks (not updated for > 15 seconds)
            const now = Date.now();
            Object.keys(tracksRef.current).forEach((key) => {
                if (now - tracksRef.current[key].lastUpdated > 15000) {
                    delete tracksRef.current[key];
                }
            });

            // A. Draw Zone Polygons
            zones.forEach((zone) => {
                const coords = zone.geometry?.coordinates?.[0];
                if (!coords || coords.length === 0) return;

                ctx.beginPath();
                coords.forEach(([xNorm, yNorm], idx) => {
                    const px = xNorm * width;
                    const py = yNorm * height;
                    if (idx === 0) ctx.moveTo(px, py);
                    else ctx.lineTo(px, py);
                });
                ctx.closePath();

                // Style zones based on type
                let fillStyle = "rgba(168, 85, 247, 0.04)"; // default purple
                let strokeStyle = "rgba(168, 85, 247, 0.2)";
                
                if (zone.zone_type === "BILLING") {
                    fillStyle = "rgba(245, 158, 11, 0.05)"; // amber
                    strokeStyle = "rgba(245, 158, 11, 0.3)";
                } else if (zone.zone_type === "ENTRANCE") {
                    fillStyle = "rgba(6, 182, 212, 0.03)"; // cyan
                    strokeStyle = "rgba(6, 182, 212, 0.2)";
                } else if (zone.zone_type === "DISPLAY") {
                    fillStyle = "rgba(59, 130, 246, 0.04)"; // blue
                    strokeStyle = "rgba(59, 130, 246, 0.25)";
                }

                ctx.fillStyle = fillStyle;
                ctx.fill();
                ctx.lineWidth = 1.5;
                ctx.strokeStyle = strokeStyle;
                ctx.setLineDash([4, 4]);
                ctx.stroke();
                ctx.setLineDash([]);

                // Draw small label at the zone centroid
                const sumCoords = coords.reduce((acc, val) => [acc[0] + val[0], acc[1] + val[1]], [0, 0]);
                const centroidX = (sumCoords[0] / coords.length) * width;
                const centroidY = (sumCoords[1] / coords.length) * height;

                ctx.font = "bold 9px sans-serif";
                ctx.fillStyle = zone.zone_type === "BILLING" 
                    ? "#d97706" 
                    : theme === "light" 
                        ? "rgba(9, 9, 11, 0.6)" 
                        : "rgba(255, 255, 255, 0.4)";
                ctx.textAlign = "center";
                ctx.fillText(zone.zone_name, centroidX, centroidY);
            });

            // B. Draw Group Connections (dashed lines between tracks with same group_id)
            const groupedTracks: Record<string, TrackPoint[]> = {};
            Object.values(tracksRef.current).forEach((track) => {
                if (track.groupId) {
                    if (!groupedTracks[track.groupId]) {
                        groupedTracks[track.groupId] = [];
                    }
                    groupedTracks[track.groupId].push(track);
                }
            });

            ctx.lineWidth = 1;
            ctx.strokeStyle = theme === "light" ? "rgba(107, 33, 168, 0.35)" : "rgba(168, 85, 247, 0.35)";
            ctx.setLineDash([2, 4]);
            Object.values(groupedTracks).forEach((groupList) => {
                if (groupList.length > 1) {
                    ctx.beginPath();
                    groupList.forEach((track, idx) => {
                        const px = track.currentX * width;
                        const py = track.currentY * height;
                        if (idx === 0) ctx.moveTo(px, py);
                        else ctx.lineTo(px, py);
                    });
                    ctx.stroke();
                }
            });
            ctx.setLineDash([]);

            // C. Smooth (LERP) and Draw Tracking Dots
            Object.values(tracksRef.current).forEach((track) => {
                // Smooth LERP factor (higher = faster, lower = smoother lag)
                const lerpFactor = 0.08;
                track.currentX += (track.targetX - track.currentX) * lerpFactor;
                track.currentY += (track.targetY - track.currentY) * lerpFactor;

                const px = track.currentX * width;
                const py = track.currentY * height;

                // Set opacity (fade if idle)
                const opacity = isIdle ? 0.2 : 0.85;

                // Draw pulse glow ring
                ctx.beginPath();
                ctx.arc(px, py, 12, 0, 2 * Math.PI);
                ctx.fillStyle = `rgba(168, 85, 247, ${opacity * 0.15})`;
                ctx.fill();

                // Draw tracking point core
                ctx.beginPath();
                ctx.arc(px, py, 5, 0, 2 * Math.PI);
                ctx.fillStyle = `rgba(168, 85, 247, ${opacity})`;
                ctx.fill();
                
                // Draw white inner dot for contrast
                ctx.beginPath();
                ctx.arc(px, py, 1.8, 0, 2 * Math.PI);
                ctx.fillStyle = `rgba(255, 255, 255, ${opacity})`;
                ctx.fill();

                // Draw track label (simulated ID or group number)
                ctx.font = "9px monospace";
                ctx.fillStyle = theme === "light" ? "rgba(9, 9, 11, 0.75)" : "rgba(255, 255, 255, 0.65)";
                ctx.textAlign = "left";
                const label = track.groupId ? `${track.id.substring(0, 5)} (Group)` : track.id.substring(0, 6);
                ctx.fillText(label, px + 8, py + 3);
            });

            // D. Render idle overlay if store is idle
            if (isIdle) {
                ctx.fillStyle = theme === "light" ? "rgba(250, 250, 250, 0.75)" : "rgba(9, 9, 11, 0.65)";
                ctx.fillRect(0, 0, width, height);

                ctx.font = "bold 13px sans-serif";
                ctx.fillStyle = theme === "light" ? "rgba(9, 9, 11, 0.85)" : "rgba(255, 255, 255, 0.75)";
                ctx.textAlign = "center";
                ctx.fillText("No Live Activity Detected", width / 2, height / 2 - 10);
                
                ctx.font = "10px sans-serif";
                ctx.fillStyle = theme === "light" ? "rgba(9, 9, 11, 0.5)" : "rgba(255, 255, 255, 0.4)";
                ctx.fillText("Monitoring feeds for entry events...", width / 2, height / 2 + 8);
            }

            animationFrameId = requestAnimationFrame(render);
        };

        render();

        return () => cancelAnimationFrame(animationFrameId);
    }, [zones, isIdle, theme]);

    return (
        <div ref={containerRef} className="relative w-full overflow-hidden rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-zinc-200 dark:bg-zinc-950 flex items-center justify-center transition-colors duration-300">
            {/* The Background layout layout image */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
                ref={imgRef}
                src={`/layouts/${layoutImage}`}
                alt="Store Layout"
                className="w-full h-auto opacity-80 dark:opacity-75 object-contain select-none transition-opacity duration-300"
                onLoad={handleImageLoad}
                draggable={false}
            />
            
            {/* Canvas overlays exact sizing */}
            <canvas
                ref={canvasRef}
                className="absolute top-0 left-0 w-full h-full pointer-events-none"
            />
        </div>
    );
}
