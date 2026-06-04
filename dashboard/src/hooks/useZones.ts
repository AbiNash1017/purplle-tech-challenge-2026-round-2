import { useState, useEffect } from "react";

export interface ZoneGeometry {
    type: string;
    coordinates: number[][][]; // GeoJSON coordinates [[[x1, y1], [x2, y2], ...]]
}

export interface ZoneConfig {
    _id: string;
    store_id: string;
    zone_id: string;
    zone_name: string;
    zone_type: string;
    is_revenue_zone: boolean | string;
    geometry: ZoneGeometry;
}

export function useZones(storeId: string, apiRoot: string = "http://localhost:8000/api/v1") {
    const [zones, setZones] = useState<ZoneConfig[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!storeId) return;

        let active = true;
        const fetchZones = async () => {
            try {
                const response = await fetch(`${apiRoot}/stores/${storeId}/zones`);
                if (!response.ok) {
                    throw new Error(`Error fetching zones: ${response.statusText}`);
                }
                const data = await response.json();
                if (active) {
                    setZones(data);
                    setError(null);
                }
            } catch (err: any) {
                if (active) {
                    setError(err.message || "Failed to fetch zones");
                }
            } finally {
                if (active) {
                    setLoading(false);
                }
            }
        };

        fetchZones();

        return () => {
            active = false;
        };
    }, [storeId, apiRoot]);

    return { zones, loading, error };
}
