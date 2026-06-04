import { useState, useEffect } from "react";

export interface CameraInfo {
    cam_id: string;
    cam_role: string;
    total_events: number;
    customers: number;
    staff: number;
    video_path: string; // e.g. "/videos/ST1076/CAM1_annotated.mp4"
}

export interface DataSourceInfo {
    store_id: string;
    source: "simulated" | "live_pipeline";
    description: string;
    processed_at: string | null;
    cameras: CameraInfo[];
    totals: Record<string, any>;
}

export function useCCTVCameras(storeId: string, apiRoot: string = "http://localhost:8000/api/v1") {
    const [dataSource, setDataSource] = useState<DataSourceInfo | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Reset state when store switches so stale data from previous store doesn't bleed through
    useEffect(() => {
        setDataSource(null);
        setLoading(true);
        setError(null);
    }, [storeId]);

    useEffect(() => {
        if (!storeId) return;

        let active = true;
        const fetchDataSource = async () => {
            try {
                const response = await fetch(`${apiRoot}/data-source/${storeId}`);
                if (!response.ok) {
                    throw new Error(`Error fetching data source info: ${response.statusText}`);
                }
                const data = await response.json();
                if (active) {
                    setDataSource(data);
                    setError(null);
                }
            } catch (err: any) {
                if (active) {
                    setError(err.message || "Failed to fetch data source info");
                }
            } finally {
                if (active) {
                    setLoading(false);
                }
            }
        };

        fetchDataSource();

        return () => {
            active = false;
        };
    }, [storeId, apiRoot]);

    return { dataSource, loading, error };
}
