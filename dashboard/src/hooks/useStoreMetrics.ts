import { useState, useEffect } from "react";

export interface ZoneHeatmapPoint {
    zone_id: string;
    zone_name: string;
    zone_type: string;
    dwell_seconds: number;
    visit_count: number;
}

export interface StoreMetrics {
    store_id: string;
    timestamp: string;
    active_customers: number;
    footfall_count: number;
    avg_dwell_seconds: number;
    queue_wait_seconds_avg: number;
    queue_abandon_rate: number;
    queue_depth: number;
    pos_correlation_rate: number;
    zone_breakdown: ZoneHeatmapPoint[];
}

export function useStoreMetrics(storeId: string, apiRoot: string = "http://localhost:8000/api/v1") {
    const [metrics, setMetrics] = useState<StoreMetrics | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!storeId) return;

        let active = true;
        const fetchMetrics = async () => {
            try {
                const response = await fetch(`${apiRoot}/metrics/${storeId}`);
                if (!response.ok) {
                    throw new Error(`Error fetching metrics: ${response.statusText}`);
                }
                const data = await response.json();
                if (active) {
                    setMetrics(data);
                    setError(null);
                }
            } catch (err: any) {
                if (active) {
                    setError(err.message || "Failed to fetch metrics");
                }
            } finally {
                if (active) {
                    setLoading(false);
                }
            }
        };

        fetchMetrics();
        const interval = setInterval(fetchMetrics, 10000); // Poll metrics every 10s

        return () => {
            active = false;
            clearInterval(interval);
        };
    }, [storeId, apiRoot]);

    return { metrics, loading, error };
}
