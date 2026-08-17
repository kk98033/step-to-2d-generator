import React, { useState, useEffect, useRef } from 'react';
import { 
  FileText, File, Folder, FolderOpen, Loader2, CheckCircle, 
  ChevronRight, ChevronDown, AlertTriangle, BookOpen, ArrowLeft, 
  Home, ZoomIn, ZoomOut, CheckSquare, Square, 
  Layers, Sparkles
} from 'lucide-react';
import axios from 'axios';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, Html } from '@react-three/drei';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import * as THREE from 'three';

const API_BASE = 'http://localhost:8000';

interface TreeNode {
  name: string;
  type: string;
  children: { [key: string]: TreeNode };
}

// --- Single Part 3D Viewer with 3D Feature Annotations ---
function SinglePartViewerWithFeatures({ 
  stlUrl, 
  featureRecords, 
  selectedFeatureIds, 
  hoveredFeatureId, 
  focusedFeatureId,
  onHoverFeature, 
  onSelectFeature 
}: {
  stlUrl: string;
  featureRecords: any[];
  selectedFeatureIds: Set<string>;
  hoveredFeatureId: string | null;
  focusedFeatureId?: string | null;
  onHoverFeature: (id: string | null) => void;
  onSelectFeature: (id: string) => void;
}) {
  const [geometry, setGeometry] = useState<THREE.BufferGeometry | null>(null);
  const [modelCenter, setModelCenter] = useState<THREE.Vector3>(new THREE.Vector3(0, 0, 0));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { camera } = useThree();

  useEffect(() => {
    if (!stlUrl) return;
    setLoading(true);
    setError(null);
    setGeometry(null);

    const loader = new STLLoader();
    loader.load(
      stlUrl,
      (geom: any) => {
        geom.computeVertexNormals();
        geom.computeBoundingBox();
        geom.computeBoundingSphere();

        const center = new THREE.Vector3();
        geom.boundingBox!.getCenter(center);
        setModelCenter(center.clone());
        geom.translate(-center.x, -center.y, -center.z);

        const sphere = geom.boundingSphere!;
        const radius = sphere.radius;
        const dist = radius * 2.8;
        (camera as THREE.PerspectiveCamera).position.set(dist, dist * 0.8, dist);
        (camera as THREE.PerspectiveCamera).near = 0.01;
        (camera as THREE.PerspectiveCamera).far = radius * 100;
        (camera as THREE.PerspectiveCamera).updateProjectionMatrix();

        setGeometry(geom);
        setLoading(false);
      },
      undefined,
      (err: any) => {
        console.error('STL load error:', err);
        setError('無法載入 3D 模型');
        setLoading(false);
      }
    );

    return () => {
      if (geometry) geometry.dispose();
    };
  }, [stlUrl]);

  if (loading) return null;
  if (error || !geometry) return null;

  return (
    <group>
      {/* 3D Model Mesh */}
      <mesh geometry={geometry}>
        <meshPhongMaterial
          color="#38bdf8"
          emissive="#082f49"
          specular="#bae6fd"
          shininess={60}
          flatShading={false}
          side={THREE.DoubleSide}
          transparent={true}
          opacity={0.88}
        />
      </mesh>

      {/* Wireframe Substructure */}
      <mesh geometry={geometry}>
        <meshBasicMaterial
          color="#0284c7"
          wireframe={true}
          transparent={true}
          opacity={0.15}
        />
      </mesh>

      {/* 3D Features Pins & Annotations & 3D Bounding Boxes */}
      {featureRecords.map((feat, idx) => {
        if (!selectedFeatureIds.has(feat.id)) return null;

        const geom = feat.geometry || {};
        let rawPos: [number, number, number] | null = null;
        let rawSize: [number, number, number] = [2.0, 1.5, 2.0];

        if (Array.isArray(geom.center) && geom.center.length >= 3) {
          rawPos = [geom.center[0], geom.center[1], geom.center[2]];
        } else if (Array.isArray(geom.point) && geom.point.length >= 3) {
          rawPos = [geom.point[0], geom.point[1], geom.point[2]];
        } else if (geom.kind === 'cone' && Array.isArray(geom.center)) {
          rawPos = [geom.center[0], geom.center[1], geom.center[2]];
        } else if (geom.kind === 'fillet' && Array.isArray(geom.mid_point)) {
          rawPos = [geom.mid_point[0], geom.mid_point[1], geom.mid_point[2]];
        } else if (typeof geom.position === 'number') {
          // Axis position along Y
          rawPos = [0, geom.position, 0];
        }

        if (Array.isArray(geom.size) && geom.size.length >= 3) {
          rawSize = [
            Math.max(0.3, Number(geom.size[0]) || 2.0),
            Math.max(0.2, Number(geom.size[1]) || 1.5),
            Math.max(0.3, Number(geom.size[2]) || 2.0),
          ];
        } else if (typeof geom.diameter === 'number') {
          const d = geom.diameter;
          const l = geom.length || geom.width || geom.height || 1.0;
          rawSize = [d, Math.max(0.3, l), d];
        } else if (geom.size && typeof geom.size === 'object') {
          rawSize = [
            Math.max(0.3, geom.size.W || 2.0),
            Math.max(0.2, geom.size.H || 2.0),
            Math.max(0.3, geom.size.D || 2.0),
          ];
        }

        if (!rawPos) return null;

        const pos: [number, number, number] = [
          rawPos[0] - modelCenter.x,
          rawPos[1] - modelCenter.y,
          rawPos[2] - modelCenter.z,
        ];

        const isHovered = hoveredFeatureId === feat.id || focusedFeatureId === feat.id;
        const tag = feat.id || `F${idx + 1}`;
        const fType = (feat.type || '').toLowerCase();

        let tagBg = '#0284c7';
        if (fType.includes('journal') || fType.includes('shaft')) tagBg = '#2563eb';
        else if (fType.includes('groove') || fType.includes('slot')) tagBg = '#9333ea';
        else if (fType.includes('cone') || fType.includes('chamfer')) tagBg = '#d97706';
        else if (fType.includes('step')) tagBg = '#ea580c';
        else if (fType.includes('fillet')) tagBg = '#db2777';
        else if (fType.includes('hole')) tagBg = '#059669';
        else if (fType.includes('datum')) tagBg = '#4f46e5';

        const boxColor = isHovered ? '#fbbf24' : tagBg;

        return (
          <group key={feat.id || idx} position={pos}>
            {/* 3D Wireframe Bounding Box (3D 框框) */}
            <mesh
              onPointerOver={(e) => { e.stopPropagation(); onHoverFeature(feat.id); }}
              onPointerOut={(e) => { e.stopPropagation(); onHoverFeature(null); }}
              onClick={(e) => { e.stopPropagation(); onSelectFeature(feat.id); }}
            >
              <boxGeometry args={[rawSize[0] * 1.08, rawSize[1] * 1.04, rawSize[2] * 1.08]} />
              <meshBasicMaterial
                color={boxColor}
                wireframe={true}
                transparent={true}
                opacity={isHovered ? 1.0 : 0.75}
              />
            </mesh>

            {/* 3D Semi-Transparent Shaded Volumetric Zone */}
            <mesh
              onPointerOver={(e) => { e.stopPropagation(); onHoverFeature(feat.id); }}
              onPointerOut={(e) => { e.stopPropagation(); onHoverFeature(null); }}
              onClick={(e) => { e.stopPropagation(); onSelectFeature(feat.id); }}
            >
              <boxGeometry args={[rawSize[0], rawSize[1], rawSize[2]]} />
              <meshStandardMaterial
                color={boxColor}
                emissive={boxColor}
                emissiveIntensity={isHovered ? 0.9 : 0.35}
                transparent={true}
                opacity={isHovered ? 0.55 : 0.25}
                roughness={0.2}
                depthWrite={false}
              />
            </mesh>

            {/* Glowing 3D Center Marker Pin */}
            <mesh position={[0, rawSize[1] / 2 + 0.1, 0]}>
              <sphereGeometry args={[isHovered ? 0.3 : 0.18, 16, 16]} />
              <meshStandardMaterial
                color={boxColor}
                emissive={boxColor}
                emissiveIntensity={isHovered ? 1.8 : 0.9}
                roughness={0.2}
              />
            </mesh>

            {/* 3D Floating HTML Label Pin */}
            <Html
              position={[0, rawSize[1] / 2 + 0.5, 0]}
              center
              distanceFactor={22}
              style={{
                pointerEvents: 'auto',
                cursor: 'pointer',
                userSelect: 'none',
                transition: 'all 0.15s ease',
                transform: isHovered ? 'scale(1.18)' : 'scale(1.0)',
              }}
            >
              <div
                onMouseEnter={() => onHoverFeature(feat.id)}
                onMouseLeave={() => onHoverFeature(null)}
                onClick={() => onSelectFeature(feat.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  background: isHovered ? 'rgba(15, 23, 42, 0.98)' : 'rgba(15, 23, 42, 0.88)',
                  border: `1.5px solid ${boxColor}`,
                  borderRadius: 6,
                  padding: '3px 8px',
                  color: '#fff',
                  fontSize: 11,
                  fontWeight: 600,
                  whiteSpace: 'nowrap',
                  boxShadow: isHovered ? '0 0 18px rgba(251, 191, 36, 0.7)' : '0 2px 8px rgba(0,0,0,0.5)',
                  backdropFilter: 'blur(4px)',
                }}
              >
                <span
                  style={{
                    fontSize: 9,
                    padding: '1px 5px',
                    borderRadius: 3,
                    background: boxColor,
                    color: isHovered ? '#000' : '#fff',
                    fontWeight: 700,
                  }}
                >
                  {tag}
                </span>
                <span>{feat.name}</span>
              </div>
            </Html>
          </group>
        );
      })}
    </group>
  );
}

// --- Single Part 3D Viewer (loads one STL at a time, imperatively) ---
function SinglePartViewer({ stlUrl }: { stlUrl: string }) {
  const [geometry, setGeometry] = useState<THREE.BufferGeometry | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { camera } = useThree();

  useEffect(() => {
    if (!stlUrl) return;
    setLoading(true);
    setError(null);
    setGeometry(null);

    const loader = new STLLoader();
    loader.load(
      stlUrl,
      (geom: any) => {
        // Recompute normals to avoid NaN issues from ASCII STL
        geom.computeVertexNormals();
        geom.computeBoundingBox();
        geom.computeBoundingSphere();

        // Auto-center the geometry
        const center = new THREE.Vector3();
        geom.boundingBox!.getCenter(center);
        geom.translate(-center.x, -center.y, -center.z);

        // Auto-fit camera
        const sphere = geom.boundingSphere!;
        const radius = sphere.radius;
        const dist = radius * 3;
        (camera as THREE.PerspectiveCamera).position.set(dist, dist, dist);
        (camera as THREE.PerspectiveCamera).near = 0.01;
        (camera as THREE.PerspectiveCamera).far = radius * 100;
        (camera as THREE.PerspectiveCamera).updateProjectionMatrix();

        setGeometry(geom);
        setLoading(false);
      },
      undefined,
      (err: any) => {
        console.error('STL load error:', err);
        setError('無法載入 3D 模型');
        setLoading(false);
      }
    );

    return () => {
      // cleanup old geometry
      if (geometry) geometry.dispose();
    };
  }, [stlUrl]);

  if (loading) return null;
  if (error || !geometry) return null;

  return (
    <mesh geometry={geometry}>
      <meshPhongMaterial
        color="#94a3b8"
        emissive="#0a0a1a"
        specular="#333333"
        shininess={40}
        flatShading={true}
        side={THREE.DoubleSide}
      />
    </mesh>
  );
}

// --- Diff Viewer (loads multiple STLs with specific materials) ---
function DiffViewer({ diffUrls, visibleLayers, viewMode, wiperValue }: { diffUrls: Record<string, string>, visibleLayers: Record<string, boolean>, viewMode: 'overlay'|'wireframe'|'wiper', wiperValue: number }) {
  const { camera } = useThree();
  const [geometries, setGeometries] = useState<Record<string, THREE.BufferGeometry>>({});
  const [bbox, setBbox] = useState<THREE.Box3 | null>(null);
  
  const planeOld = React.useMemo(() => new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0), []);
  const planeNew = React.useMemo(() => new THREE.Plane(new THREE.Vector3(1, 0, 0), 0), []);

  if (bbox && viewMode === 'wiper') {
      const xRange = bbox.max.x - bbox.min.x;
      const clipX = bbox.min.x + (xRange * wiperValue) / 100;
      planeOld.constant = clipX;
      planeNew.constant = -clipX;
  }
  
  useEffect(() => {
    const loader = new STLLoader();
    const newGeoms: Record<string, THREE.BufferGeometry> = {};
    let loadedCount = 0;
    const totalToLoad = Object.keys(diffUrls).length;
    
    if (totalToLoad === 0) return;
    
    // Create an overall bounding box
    const overallBox = new THREE.Box3();
    
    Object.entries(diffUrls).forEach(([key, url]) => {
      loader.load(
        url,
        (geom: any) => {
          geom.computeVertexNormals();
          geom.computeBoundingBox();
          overallBox.union(geom.boundingBox);
          newGeoms[key] = geom;
          loadedCount++;
          
          if (loadedCount === totalToLoad) {
            // Auto-center based on overall box
            const center = new THREE.Vector3();
            overallBox.getCenter(center);
            
            Object.values(newGeoms).forEach(g => {
              g.translate(-center.x, -center.y, -center.z);
            });
            
            // Recompute overall after translate
            overallBox.translate(center.clone().negate());
            
            // Fit camera
            const sphere = new THREE.Sphere();
            overallBox.getBoundingSphere(sphere);
            const radius = sphere.radius;
            const dist = radius * 3;
            (camera as THREE.PerspectiveCamera).position.set(dist, dist, dist);
            (camera as THREE.PerspectiveCamera).near = 0.01;
            (camera as THREE.PerspectiveCamera).far = radius * 100;
            (camera as THREE.PerspectiveCamera).updateProjectionMatrix();
            
            setGeometries(newGeoms);
            setBbox(overallBox);
          }
        },
        undefined,
        (err) => {
          console.error(`Failed to load STL layer ${key}:`, err);
          loadedCount++;
          if (loadedCount === totalToLoad && Object.keys(newGeoms).length > 0) {
            setGeometries(newGeoms);
            setBbox(overallBox);
          }
        }
      );
    });
    
    return () => {
      Object.values(newGeoms).forEach(g => g.dispose());
    };
  }, [diffUrls, camera]);

  return (
    <group>
      {geometries['unchanged'] && visibleLayers['unchanged'] && (
        <mesh geometry={geometries['unchanged']}>
          <meshPhongMaterial color="#888888" transparent={true} opacity={0.3} flatShading={true} side={THREE.DoubleSide} />
        </mesh>
      )}
      {geometries['added'] && visibleLayers['added'] && (
        <mesh geometry={geometries['added']}>
          <meshPhongMaterial 
             color="#22c55e" 
             transparent={true} 
             opacity={viewMode === 'wireframe' ? 0.9 : 0.7} 
             shininess={80} 
             flatShading={true} 
             side={THREE.DoubleSide} 
             clippingPlanes={viewMode === 'wiper' ? [planeNew] : []}
          />
        </mesh>
      )}
      {geometries['removed'] && visibleLayers['removed'] && (
        <mesh geometry={geometries['removed']}>
          <meshPhongMaterial 
             color="#ef4444" 
             transparent={true} 
             opacity={viewMode === 'wireframe' ? 0.3 : 0.7} 
             shininess={80} 
             flatShading={true} 
             side={THREE.DoubleSide} 
             wireframe={viewMode === 'wireframe'}
             clippingPlanes={viewMode === 'wiper' ? [planeOld] : []}
          />
        </mesh>
      )}
    </group>
  );
}

// --- Tree Component ---
function TreeNode({ node, onSelect, selectedPart }: any) {
  const [expanded, setExpanded] = useState(true);
  const isLeaf = !node.children || node.children.length === 0;
  const isSelected = selectedPart === node.file_prefix;

  // diff colors
  let diffColor = '#aaa';
  let bgColor = 'transparent';
  if (node.diffStatus === 'added') {
    diffColor = '#22c55e';
    bgColor = 'rgba(34, 197, 94, 0.15)';
  } else if (node.diffStatus === 'removed') {
    diffColor = '#ef4444';
    bgColor = 'rgba(239, 68, 68, 0.15)';
  }

  return (
    <div style={{ paddingLeft: 16 }}>
      <div
        onClick={(e) => {
          e.stopPropagation();
          if (!isLeaf) setExpanded(!expanded);
          if (node.file_prefix) onSelect(node.file_prefix);
        }}
        style={{
          display: 'flex',
          alignItems: 'center',
          padding: '4px 8px',
          cursor: 'pointer',
          borderRadius: 4,
          fontSize: 13,
          background: isSelected ? '#3B82F6' : bgColor,
          color: isSelected ? '#fff' : diffColor,
          border: node.diffStatus && node.diffStatus !== 'unchanged' ? `1px solid ${diffColor}55` : '1px solid transparent',
          marginBottom: 2,
          minWidth: 0
        }}
        onMouseEnter={(e) => { if (!isSelected) (e.currentTarget as HTMLDivElement).style.background = node.diffStatus && node.diffStatus !== 'unchanged' ? bgColor : '#1a1a1a'; }}
        onMouseLeave={(e) => { if (!isSelected) (e.currentTarget as HTMLDivElement).style.background = bgColor; }}
      >
        <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center' }}>
          {!isLeaf ? (expanded ? <ChevronDown size={14} style={{ marginRight: 4 }} /> : <ChevronRight size={14} style={{ marginRight: 4 }} />) : <span style={{ width: 18 }} />}
          {!isLeaf ? (expanded ? <FolderOpen size={16} style={{ marginRight: 8, color: '#eab308' }} /> : <Folder size={16} style={{ marginRight: 8, color: '#eab308' }} />) : <FileText size={16} style={{ marginRight: 8, color: '#60a5fa' }} />}
        </div>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{node.name}</span>
      </div>
      {expanded && !isLeaf && (
        <div>
          {node.children.map((child: any, idx: number) => (
            <TreeNode key={idx} node={child} onSelect={onSelect} selectedPart={selectedPart} />
          ))}
        </div>
      )}
    </div>
  );
}

// --- Example Tree Node ---
function ExampleTreeNode({ node, onSelect, selectedExample }: any) {
  const [expanded, setExpanded] = useState(
    node.name === '業主範例圖 (Reference)' ||
    node.name === 'FAN 20260625 已處理工程圖'
  );
  const isLeaf = node.type === 'file';
  const isSelected = selectedExample?.url === node.url;

  return (
    <div style={{ paddingLeft: node.name === '業主範例圖 (Reference)' ? 0 : 16 }}>
      <div
        onClick={(e) => {
          e.stopPropagation();
          if (!isLeaf) setExpanded(!expanded);
          else onSelect(node);
        }}
        style={{
          display: 'flex',
          alignItems: 'center',
          padding: '6px 8px',
          cursor: 'pointer',
          borderRadius: 6,
          fontSize: 13,
          background: isSelected ? '#3B82F6' : 'transparent',
          color: isSelected ? '#fff' : '#ccc',
          marginBottom: 2
        }}
        onMouseEnter={(e) => { if (!isSelected) (e.currentTarget as HTMLDivElement).style.background = '#1a1a1a'; }}
        onMouseLeave={(e) => { if (!isSelected) (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
      >
        {!isLeaf ? (expanded ? <ChevronDown size={14} style={{ marginRight: 4 }} /> : <ChevronRight size={14} style={{ marginRight: 4 }} />) : <span style={{ width: 18 }} />}
        {!isLeaf ? (expanded ? <FolderOpen size={16} style={{ marginRight: 8, color: '#eab308' }} /> : <Folder size={16} style={{ marginRight: 8, color: '#eab308' }} />) : <FileText size={16} style={{ marginRight: 8, color: '#a78bfa' }} />}
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{node.display_name || node.name}</span>
      </div>
      {expanded && !isLeaf && node.children && (
        <div>
          {node.children.map((child: any, idx: number) => (
            <ExampleTreeNode key={idx} node={child} onSelect={onSelect} selectedExample={selectedExample} />
          ))}
        </div>
      )}
    </div>
  );
}

// --- Main App ---
function App() {
  const [uploadMode, setUploadMode] = useState<'single' | 'diff'>('single');
  const [visibleLayers, setVisibleLayers] = useState({ added: true, removed: true, unchanged: true });
  const [viewMode, setViewMode] = useState<'overlay' | 'wireframe' | 'wiper'>('overlay');
  const [activeTab, setActiveTab] = useState<'layers' | 'data' | 'tree'>('layers');
  const [wiperValue, setWiperValue] = useState<number>(50);
  
  const [file, setFile] = useState<globalThis.File | null>(null);
  const [fileOld, setFileOld] = useState<globalThis.File | null>(null);
  const [fileNew, setFileNew] = useState<globalThis.File | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [progressMsg, setProgressMsg] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const [progress, setProgress] = useState({ current: 0, total: 0 });

  const [results, setResults] = useState<any>(null);
  const [selectedPart, setSelectedPart] = useState<string | null>(null);
  const [existingModels, setExistingModels] = useState<any[]>([]);
  const [exampleTree, setExampleTree] = useState<any>(null);
  const [processedTree, setProcessedTree] = useState<any>(null);
  const [selectedExample, setSelectedExample] = useState<any>(null);
  const [viewerTitle, setViewerTitle] = useState('公司範例圖');
  const [zoom, setZoom] = useState(1);
  const [showFeatureLayer, setShowFeatureLayer] = useState(false);
  const [featureRecords, setFeatureRecords] = useState<any[]>([]);
  const [selectedFeatureIds, setSelectedFeatureIds] = useState<Set<string>>(new Set());
  const [hoveredFeatureId, setHoveredFeatureId] = useState<string | null>(null);
  const [focusedFeatureId, setFocusedFeatureId] = useState<string | null>(null);
  const [featureFilter, setFeatureFilter] = useState<string>('ALL');
  const [featureSearch, setFeatureSearch] = useState<string>('');

  // --- Tree Diff State ---
  const [diffedTreeOld, setDiffedTreeOld] = useState<any>(null);
  const [diffedTreeNew, setDiffedTreeNew] = useState<any>(null);

  useEffect(() => {
    if (results?.tree_old && results?.tree_new) {
      const getPathCounts = (n: any, p: string, res = new Map<string, number>()) => {
        const cp = p + "/" + n.name;
        res.set(cp, (res.get(cp) || 0) + 1);
        n.children?.forEach((c: any) => getPathCounts(c, cp, res));
        return res;
      };
      
      const oldCounts = getPathCounts(results.tree_old, "");
      const newCounts = getPathCounts(results.tree_new, "");

      const markNode = (n: any, p: string, otherCounts: Map<string, number>, isOld: boolean, myTracker: Map<string, number>): any => {
        const cp = p + "/" + n.name;
        const index = (myTracker.get(cp) || 0) + 1;
        myTracker.set(cp, index);

        const otherTotal = otherCounts.get(cp) || 0;
        let status = 'unchanged';
        if (index > otherTotal) {
          status = isOld ? 'removed' : 'added';
        }

        return { 
          ...n, 
          diffStatus: status, 
          children: n.children?.map((c: any) => markNode(c, cp, otherCounts, isOld, myTracker)) 
        };
      };

      setDiffedTreeOld(markNode(results.tree_old, "", newCounts, true, new Map()));
      setDiffedTreeNew(markNode(results.tree_new, "", oldCounts, false, new Map()));
    }
  }, [results]);

  useEffect(() => {
    setShowFeatureLayer(false);
    setFeatureRecords([]);
    setSelectedFeatureIds(new Set());

    const partsMapForFeatures: Record<string, any> = results?.parts_map || {};
    const selectedData = selectedPart ? partsMapForFeatures[selectedPart] : null;
    if (!selectedData?.features_json) return;

    axios.get(`${API_BASE}${selectedData.features_json}?t=${Date.now()}`)
      .then(res => {
        const records = Array.isArray(res.data) ? res.data : [];
        setFeatureRecords(records);
        setSelectedFeatureIds(new Set(records.map((r: any) => r.id)));
      })
      .catch(err => {
        console.error('Feature records load error:', err);
        setFeatureRecords([]);
        setSelectedFeatureIds(new Set());
      });
  }, [results, selectedPart]);

  const toggleFeature = (id: string) => {
    setSelectedFeatureIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllFeatures = (ids?: string[]) => {
    if (ids && ids.length > 0) {
      setSelectedFeatureIds(prev => {
        const next = new Set(prev);
        ids.forEach(i => next.add(i));
        return next;
      });
    } else {
      setSelectedFeatureIds(new Set(featureRecords.map(f => f.id)));
    }
  };

  const deselectAllFeatures = (ids?: string[]) => {
    if (ids && ids.length > 0) {
      setSelectedFeatureIds(prev => {
        const next = new Set(prev);
        ids.forEach(i => next.delete(i));
        return next;
      });
    } else {
      setSelectedFeatureIds(new Set());
    }
  };

  // Loading dots
  const [dots, setDots] = useState('');
  useEffect(() => {
    if (status === 'processing') {
      const id = setInterval(() => {
        setDots(d => d.length >= 3 ? '' : d + '.');
      }, 500);
      return () => clearInterval(id);
    }
  }, [status]);

  // --- Resizable Sidebar State ---
  const [sidebarWidth, setSidebarWidth] = useState<number>(320);
  const isResizing = useRef(false);

  // --- Sync Scroll State ---
  const treeOldRef = useRef<HTMLDivElement>(null);
  const treeNewRef = useRef<HTMLDivElement>(null);
  const isSyncingLeftScroll = useRef(false);
  const isSyncingRightScroll = useRef(false);

  const handleTreeOldScroll = (e: any) => {
    if (!isSyncingLeftScroll.current && treeNewRef.current) {
      isSyncingRightScroll.current = true;
      treeNewRef.current.scrollTop = e.target.scrollTop;
    }
    isSyncingLeftScroll.current = false;
  };

  const handleTreeNewScroll = (e: any) => {
    if (!isSyncingRightScroll.current && treeOldRef.current) {
      isSyncingLeftScroll.current = true;
      treeOldRef.current.scrollTop = e.target.scrollTop;
    }
    isSyncingRightScroll.current = false;
  };

  const handleMouseMove = React.useCallback((e: MouseEvent) => {
    if (!isResizing.current) return;
    setSidebarWidth(Math.max(260, Math.min(e.clientX, window.innerWidth - 50)));
  }, []);

  const handleMouseUp = React.useCallback(() => {
    isResizing.current = false;
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', handleMouseUp);
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
  }, [handleMouseMove]);

  const startResizing = React.useCallback(() => {
    isResizing.current = true;
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
  }, [handleMouseMove, handleMouseUp]);

  useEffect(() => {
    if (status === 'idle' || !status) {
      axios.get(`${API_BASE}/api/models`).then(res => {
        setExistingModels(res.data.models || []);
      }).catch(err => console.error(err));
      axios.get(`${API_BASE}/api/examples`).then(res => {
        setExampleTree(res.data.example_tree || null);
      }).catch(err => console.error(err));
      axios.get(`${API_BASE}/api/processed/fan-20260625`).then(res => {
        setProcessedTree(res.data.processed_tree || null);
      }).catch(err => console.error(err));
    }
  }, [status]);

  // Poll for status
  useEffect(() => {
    let interval: any;
    if (status === 'processing' && jobId) {
      interval = setInterval(async () => {
        try {
          const res = await axios.get(`${API_BASE}/api/status/${jobId}`);
          setProgressMsg(res.data.message);
          const nextProgress = res.data.progress || {};
          setProgress({
            current: nextProgress.current ?? res.data.current ?? 0,
            total: nextProgress.total ?? res.data.total ?? 0,
          });
          if (res.data.logs) {
            setLogs(res.data.logs);
          }

          if (res.data.status === 'completed') {
            clearInterval(interval);
            fetchResults(jobId);
          } else if (res.data.status === 'error') {
            clearInterval(interval);
            setStatus('error');
          }
        } catch (err) {
          console.error(err);
        }
      }, 1000);
    }
    return () => clearInterval(interval);
  }, [status, jobId]);

  const fetchResults = async (id: string) => {
    try {
      const res = await axios.get(`${API_BASE}/api/results/${id}`);
      setResults(res.data);
      if (res.data.tree && res.data.tree.file_prefix) {
        setSelectedPart(res.data.tree.file_prefix);
      }
      setStatus('completed');
    } catch (err: any) {
      console.error(err);
      setStatus('error');
      setProgressMsg(`取得結果失敗: ${err.message || String(err)}`);
    }
  };

  const loadExistingModel = async (modelId: string) => {
    try {
      setStatus('processing');
      setProgressMsg("載入模型資料中...");
      const res = await axios.get(`${API_BASE}/api/model/${modelId}`);
      setResults(res.data);
      if (res.data.tree && res.data.tree.file_prefix) {
        setSelectedPart(res.data.tree.file_prefix);
      }
      setStatus('completed');
    } catch (err) {
      console.error(err);
      setStatus('error');
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setStatus('uploading');
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await axios.post(`${API_BASE}/api/upload`, formData);
      setJobId(res.data.job_id);
      setStatus('processing');
    } catch (err: any) {
      console.error(err);
      setStatus('error');
      setProgressMsg(`上傳失敗: ${err.message || String(err)}`);
    }
  };

  const handleDiffUpload = async () => {
    if (!fileOld || !fileNew) return;
    setStatus('uploading');
    const formData = new FormData();
    formData.append('file_old', fileOld);
    formData.append('file_new', fileNew);
    try {
      const res = await axios.post(`${API_BASE}/api/compare`, formData);
      setJobId(res.data.job_id);
      setStatus('processing');
    } catch (err: any) {
      console.error(err);
      setStatus('error');
      setProgressMsg(`比對上傳失敗: ${err.message || String(err)}`);
    }
  };

  // --- Initial Upload Page ---
  if (status === 'idle' || !status) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#0A0A0A', padding: 24, overflowY: 'auto' }}>
        {/* FC Logo and Title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 40, margin: '0 auto' }}>
          <div style={{ width: 48, height: 48, background: '#3B82F6', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontWeight: 800, fontSize: 22, boxShadow: '0 4px 20px rgba(59,130,246,0.4)' }}>
            FC
          </div>
          <h1 style={{ fontSize: 28, fontWeight: 700, color: '#fff', margin: 0, letterSpacing: 0.5 }}>Auto 2D Drawing System</h1>
        </div>

        {/* Upload Container */}
        <div style={{ maxWidth: 480, width: '100%', background: '#171717', padding: 32, borderRadius: 12, border: '1px solid #262626', boxShadow: '0 25px 50px rgba(0,0,0,0.5)', textAlign: 'center', margin: '0 auto' }}>
          <div style={{ width: 64, height: 64, background: 'rgba(59,130,246,0.1)', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 24px' }}>
            <File color="#3B82F6" size={32} />
          </div>
          <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 8 }}>FORCECON Auto 2D</h1>
          {/* Mode Toggle */}
          <div style={{ display: 'flex', background: '#222', borderRadius: 8, padding: 4, marginBottom: 24 }}>
            <button onClick={() => setUploadMode('single')} style={{ flex: 1, padding: '8px 0', borderRadius: 6, border: 'none', background: uploadMode === 'single' ? '#3B82F6' : 'transparent', color: uploadMode === 'single' ? '#fff' : '#888', fontWeight: 600, cursor: 'pointer', transition: 'all 0.2s' }}>單一模型轉換</button>
            <button onClick={() => setUploadMode('diff')} style={{ flex: 1, padding: '8px 0', borderRadius: 6, border: 'none', background: uploadMode === 'diff' ? '#3B82F6' : 'transparent', color: uploadMode === 'diff' ? '#fff' : '#888', fontWeight: 600, cursor: 'pointer', transition: 'all 0.2s' }}>新舊版本 3D 比對</button>
          </div>

          {uploadMode === 'single' ? (
            <>
              <p style={{ color: '#999', marginBottom: 32 }}>上傳 STEP 組合件模型，自動產出 2D 工程圖</p>
              <label style={{ display: 'block', width: '100%', border: '2px dashed #333', borderRadius: 8, padding: 32, cursor: 'pointer', marginBottom: 16, transition: 'border-color 0.2s' }}>
                <input type="file" style={{ display: 'none' }} accept=".stp,.step" onChange={(e) => setFile(e.target.files?.[0] || null)} />
                {file ? (
                  <span style={{ color: '#fff', fontWeight: 500 }}>{file.name}</span>
                ) : (
                  <span style={{ color: '#666' }}>點擊選擇 STEP 檔案</span>
                )}
              </label>

              <button
                onClick={handleUpload}
                disabled={!file}
                style={{ width: '100%', background: file ? '#3B82F6' : '#333', color: '#fff', fontWeight: 500, padding: '12px 0', borderRadius: 8, border: 'none', cursor: file ? 'pointer' : 'not-allowed', fontSize: 15, marginBottom: 24 }}
              >
                上傳並處理新模型
              </button>
            </>
          ) : (
            <>
              <p style={{ color: '#999', marginBottom: 32 }}>上傳新舊版本的 STEP 模型，自動進行 3D 布林差異分析</p>
              <div style={{ display: 'flex', gap: 16, marginBottom: 16 }}>
                <label style={{ flex: 1, border: '2px dashed #333', borderRadius: 8, padding: '24px 16px', cursor: 'pointer', transition: 'border-color 0.2s' }}>
                  <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>舊版模型 (Old)</div>
                  <input type="file" style={{ display: 'none' }} accept=".stp,.step" onChange={(e) => setFileOld(e.target.files?.[0] || null)} />
                  {fileOld ? <span style={{ color: '#fff', fontSize: 13, wordBreak: 'break-all' }}>{fileOld.name}</span> : <span style={{ color: '#666', fontSize: 13 }}>選擇 STEP</span>}
                </label>
                <label style={{ flex: 1, border: '2px dashed #333', borderRadius: 8, padding: '24px 16px', cursor: 'pointer', transition: 'border-color 0.2s' }}>
                  <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>新版模型 (New)</div>
                  <input type="file" style={{ display: 'none' }} accept=".stp,.step" onChange={(e) => setFileNew(e.target.files?.[0] || null)} />
                  {fileNew ? <span style={{ color: '#fff', fontSize: 13, wordBreak: 'break-all' }}>{fileNew.name}</span> : <span style={{ color: '#666', fontSize: 13 }}>選擇 STEP</span>}
                </label>
              </div>

              <button
                onClick={handleDiffUpload}
                disabled={!fileOld || !fileNew}
                style={{ width: '100%', background: (fileOld && fileNew) ? '#3B82F6' : '#333', color: '#fff', fontWeight: 500, padding: '12px 0', borderRadius: 8, border: 'none', cursor: (fileOld && fileNew) ? 'pointer' : 'not-allowed', fontSize: 15, marginBottom: 24 }}
              >
                開始 3D 差異分析
              </button>
            </>
          )}

          {existingModels.length > 0 && (
            <div style={{ textAlign: 'left', borderTop: '1px solid #262626', paddingTop: 24 }}>
              <h3 style={{ fontSize: 12, fontWeight: 600, color: '#666', marginBottom: 12, textTransform: 'uppercase', letterSpacing: 1 }}>直接載入已轉換的模型</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 200, overflowY: 'auto' }}>
                {existingModels.map((m: any) => (
                  <button
                    key={m.id}
                    onClick={() => loadExistingModel(m.id)}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left', padding: 12, borderRadius: 8, background: '#222', border: 'none', color: '#ccc', cursor: 'pointer', fontSize: 14 }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = '#333'; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = '#222'; }}
                  >
                    <Folder color="#60a5fa" size={18} />
                    <span>{m.name}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {exampleTree && (
            <div style={{ textAlign: 'left', borderTop: '1px solid #262626', paddingTop: 24, marginTop: 16 }}>
              <h3 style={{ fontSize: 12, fontWeight: 600, color: '#666', marginBottom: 12, textTransform: 'uppercase', letterSpacing: 1 }}>
                <BookOpen size={14} style={{ display: 'inline', marginRight: 6, verticalAlign: 'middle' }} />
                公司範例圖 (Reference)
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 300, overflowY: 'auto', background: '#111', padding: 8, borderRadius: 8, border: '1px solid #2d2d4a' }}>
                <ExampleTreeNode 
                  node={exampleTree} 
                  onSelect={(node: any) => { setSelectedExample(node); setViewerTitle('公司範例圖'); setStatus('viewing_example'); }} 
                  selectedExample={selectedExample} 
                />
              </div>
            </div>
          )}

          {processedTree && (
            <div style={{ textAlign: 'left', borderTop: '1px solid #262626', paddingTop: 24, marginTop: 16 }}>
              <h3 style={{ fontSize: 12, fontWeight: 600, color: '#666', marginBottom: 12, textTransform: 'uppercase', letterSpacing: 1 }}>
                <Folder size={14} style={{ display: 'inline', marginRight: 6, verticalAlign: 'middle' }} />
                已批次處理 FAN 20260625
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 300, overflowY: 'auto', background: '#111', padding: 8, borderRadius: 8, border: '1px solid #1f3b2f' }}>
                <ExampleTreeNode
                  node={processedTree}
                  onSelect={(node: any) => { setSelectedExample(node); setViewerTitle('FAN 20260625 已處理工程圖'); setStatus('viewing_example'); }}
                  selectedExample={selectedExample}
                />
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // --- Example PDF Viewer ---
  if (status === 'viewing_example' && selectedExample) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#0A0A0A', color: '#fff' }}>
        <header style={{ height: 56, borderBottom: '1px solid #262626', background: '#171717', display: 'flex', alignItems: 'center', padding: '0 16px', justifyContent: 'space-between', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button
              onClick={() => { setStatus('idle'); setSelectedExample(null); setZoom(1); }}
              style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '6px 12px', background: '#333', border: 'none', borderRadius: 6, color: '#fff', cursor: 'pointer', fontSize: 13 }}
            >
              <ArrowLeft size={16} /> 返回
            </button>
            <BookOpen size={18} color="#a78bfa" />
            <span style={{ fontWeight: 600, fontSize: 16 }}>{viewerTitle}</span>
          </div>
          <span style={{ fontSize: 14, color: '#aaa' }}>{selectedExample.display_name}</span>
        </header>
        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          {/* Left: Example list */}
          <div style={{ width: 340, borderRight: '1px solid #262626', background: '#111', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: 12, borderBottom: '1px solid #262626', fontSize: 11, fontWeight: 600, color: '#555', textTransform: 'uppercase', letterSpacing: 1 }}>
              圖面目錄
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
              {exampleTree && (
                <ExampleTreeNode 
                  node={viewerTitle.includes('FAN 20260625') ? processedTree : exampleTree} 
                  onSelect={(node: any) => setSelectedExample(node)} 
                  selectedExample={selectedExample} 
                />
              )}
            </div>
          </div>
          {/* Right: PDF/SVG Viewer */}
          <div style={{ flex: 1, display: 'flex', background: '#111', position: 'relative' }}>
            {/* Zoom Controls */}
            {selectedExample.url.endsWith('.svg') && (
              <div style={{ position: 'absolute', right: 20, top: 20, zIndex: 10, display: 'flex', gap: 8, background: '#222', padding: 8, borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.5)' }}>
                <button onClick={() => setZoom(z => z + 0.2)} style={{ background: '#333', border: 'none', color: '#fff', borderRadius: 4, width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}><ZoomIn size={16} /></button>
                <button onClick={() => setZoom(1)} style={{ background: '#333', border: 'none', color: '#fff', borderRadius: 4, width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', fontSize: 12, fontWeight: 'bold' }}>1x</button>
                <button onClick={() => setZoom(z => Math.max(0.2, z - 0.2))} style={{ background: '#333', border: 'none', color: '#fff', borderRadius: 4, width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}><ZoomOut size={16} /></button>
              </div>
            )}
            
            <div style={{ flex: 1, overflow: 'auto', display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-start', padding: 24 }}>
              {selectedExample.url.endsWith('.svg') ? (
                <img
                  key={selectedExample.filename}
                  src={`${API_BASE}${selectedExample.url}`}
                  alt="Example SVG"
                  style={{ 
                    width: `${100 * zoom}%`, 
                    minWidth: 400,
                    maxWidth: 'none',
                    height: 'auto',
                    flexShrink: 0,
                    margin: 'auto',
                    transition: 'width 0.2s cubic-bezier(0.25, 0.8, 0.25, 1)', 
                    objectFit: 'contain',
                    background: '#fff', // White background for SVG transparency
                    boxShadow: '0 10px 40px rgba(0,0,0,0.3)',
                    borderRadius: 4
                  }}
                />
              ) : selectedExample.url.endsWith('.dxf') ? (
                <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ccc' }}>
                  <div style={{ maxWidth: 420, textAlign: 'center', background: '#171717', border: '1px solid #333', borderRadius: 8, padding: 24 }}>
                    <FileText size={40} color="#60a5fa" style={{ marginBottom: 12 }} />
                    <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>{selectedExample.filename}</div>
                    <div style={{ fontSize: 13, color: '#888', lineHeight: 1.5, marginBottom: 18 }}>DXF 已保留原資料夾結構，可用 CAD/ODA Viewer 開啟。</div>
                    <a href={`${API_BASE}${selectedExample.url}`} target="_blank" style={{ display: 'inline-flex', padding: '8px 14px', background: '#3B82F6', color: '#fff', borderRadius: 6, textDecoration: 'none', fontSize: 13 }}>開啟 / 下載 DXF</a>
                  </div>
                </div>
              ) : (
                <iframe
                  key={selectedExample.filename}
                  src={`${API_BASE}${selectedExample.url}#view=FitH`}
                  title="Example PDF"
                  style={{ width: '100%', height: '100%', border: 'none' }}
                />
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // --- Processing Page ---
  if (status === 'uploading' || status === 'processing') {
    const percent = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', background: '#0A0A0A' }}>
        <div style={{ maxWidth: 440, width: '100%', background: '#171717', padding: 32, borderRadius: 12, border: '1px solid #262626', boxShadow: '0 25px 50px rgba(0,0,0,0.5)', textAlign: 'center' }}>
          <Loader2 size={48} color="#3B82F6" style={{ display: 'block', margin: '0 auto 24px', animation: 'spin 1s linear infinite' }} />
          <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 16 }}>{status === 'uploading' ? '上傳中...' : '處理中...'}</h2>
          <div style={{ width: '100%', background: '#333', borderRadius: 999, height: 12, marginBottom: 8, overflow: 'hidden' }}>
            <div style={{ 
              backgroundSize: '1.5rem 1.5rem',
              backgroundImage: 'linear-gradient(45deg, rgba(255,255,255,0.15) 25%, transparent 25%, transparent 50%, rgba(255,255,255,0.15) 50%, rgba(255,255,255,0.15) 75%, transparent 75%, transparent)', 
              backgroundColor: '#3B82F6', 
              height: 12, 
              borderRadius: 999, 
              transition: 'width 0.3s', 
              width: `${Math.max(2, percent)}%`,
              animation: 'progress-stripes 1s linear infinite'
            }} />
          </div>
          <p style={{ fontSize: 13, color: '#888', minHeight: 20 }}>{progressMsg}{dots}</p>
          {logs.length > 0 && (
            <div style={{ marginTop: 24, textAlign: 'left', background: '#0a0a0a', border: '1px solid #333', borderRadius: 8, padding: 12, height: 160, overflowY: 'auto', fontSize: 12, color: '#aaa', fontFamily: 'monospace' }}>
              {logs.map((log, idx) => (
                <div key={idx} style={{ marginBottom: 4 }}>
                  <span style={{ color: '#3B82F6', marginRight: 8 }}>[{new Date().toLocaleTimeString()}]</span>
                  {log}
                </div>
              ))}
              {status === 'processing' && (
                <div style={{ animation: 'pulse 1.5s infinite', color: '#666', marginTop: 8 }}>_</div>
              )}
            </div>
          )}
        </div>
        <style>{`
          @keyframes spin { to { transform: rotate(360deg); } }
          @keyframes progress-stripes { from { background-position: 1.5rem 0; } to { background-position: 0 0; } }
          @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0; } 100% { opacity: 1; } }
        `}</style>
      </div>
    );
  }

  // --- Error State ---
  if (status === 'error') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', background: '#0A0A0A', color: '#fff' }}>
        <div style={{ maxWidth: 440, width: '100%', background: '#171717', padding: 32, borderRadius: 12, border: '1px solid #ef4444', boxShadow: '0 25px 50px rgba(239,68,68,0.1)', textAlign: 'center' }}>
          <AlertTriangle size={48} color="#ef4444" style={{ display: 'block', margin: '0 auto 24px' }} />
          <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 16 }}>處理發生錯誤</h2>
          <p style={{ fontSize: 14, color: '#aaa', marginBottom: 24, minHeight: 40 }}>{progressMsg}</p>
          <button 
            onClick={() => { setStatus('idle'); setFile(null); setFileOld(null); setFileNew(null); setJobId(null); }}
            style={{ width: '100%', background: '#ef4444', color: '#fff', fontWeight: 500, padding: '10px 0', borderRadius: 8, border: 'none', cursor: 'pointer', fontSize: 14 }}
          >
            返回首頁
          </button>
        </div>
      </div>
    );
  }

  // --- Diff Result State ---
  if (results?.diff_result) {
    const diffUrls: Record<string, string> = {};
    for (const [k, v] of Object.entries(results.diff_result)) {
      diffUrls[k] = `${API_BASE}${String(v)}`;
    }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#0A0A0A', color: '#fff' }}>
        <header style={{ height: 56, borderBottom: '1px solid #262626', background: '#171717', display: 'flex', alignItems: 'center', padding: '0 16px', justifyContent: 'space-between', flexShrink: 0 }}>
          <div 
            onClick={() => { setStatus('idle'); setFileOld(null); setFileNew(null); setResults(null); }}
            style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', opacity: 0.9, transition: 'opacity 0.2s' }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.opacity = '1'; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.opacity = '0.9'; }}
          >
            <div style={{ width: 32, height: 32, background: '#3B82F6', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 14 }}>FC</div>
            <span style={{ fontWeight: 600, fontSize: 18, letterSpacing: 0.5 }}>Auto 2D Drawing System</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#888' }}>
            <CheckCircle size={16} color="#22c55e" />
            <span>3D 比對完成</span>
            <button onClick={() => { setStatus('idle'); setFileOld(null); setFileNew(null); setResults(null); }} style={{ display: 'flex', alignItems: 'center', gap: 4, marginLeft: 16, padding: '6px 12px', background: '#333', border: 'none', borderRadius: 4, color: '#fff', cursor: 'pointer', fontSize: 13 }}>
              <Home size={14} /> 返回首頁
            </button>
          </div>
        </header>

        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          {/* Left Panel: Tabs & Controls */}
          <div style={{ width: sidebarWidth, minWidth: 260, flexShrink: 0, borderRight: '1px solid #262626', background: '#111', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', borderBottom: '1px solid #262626', background: '#0a0a0a' }}>
              <div onClick={() => setActiveTab('layers')} style={{ flex: 1, padding: '12px 0', textAlign: 'center', fontSize: 13, fontWeight: 600, cursor: 'pointer', borderBottom: activeTab === 'layers' ? '2px solid #3B82F6' : '2px solid transparent', color: activeTab === 'layers' ? '#fff' : '#666' }}>圖層與模式</div>
              <div onClick={() => setActiveTab('data')} style={{ flex: 1, padding: '12px 0', textAlign: 'center', fontSize: 13, fontWeight: 600, cursor: 'pointer', borderBottom: activeTab === 'data' ? '2px solid #3B82F6' : '2px solid transparent', color: activeTab === 'data' ? '#fff' : '#666' }}>數據差異</div>
              <div onClick={() => setActiveTab('tree')} style={{ flex: 1, padding: '12px 0', textAlign: 'center', fontSize: 13, fontWeight: 600, cursor: 'pointer', borderBottom: activeTab === 'tree' ? '2px solid #3B82F6' : '2px solid transparent', color: activeTab === 'tree' ? '#fff' : '#666' }}>模型樹</div>
            </div>
            
            <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
              {activeTab === 'layers' && (
                <>
                  <h3 style={{ fontSize: 13, fontWeight: 600, color: '#aaa', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 16 }}>顯示模式 (View Mode)</h3>
                  <div style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
                    <button onClick={() => setViewMode('overlay')} style={{ flex: 1, padding: '8px 0', background: viewMode === 'overlay' ? '#3B82F6' : '#222', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, cursor: 'pointer' }}>一般疊加</button>
                    <button onClick={() => setViewMode('wireframe')} style={{ flex: 1, padding: '8px 0', background: viewMode === 'wireframe' ? '#3B82F6' : '#222', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, cursor: 'pointer' }}>實體+線框</button>
                    <button onClick={() => setViewMode('wiper')} style={{ flex: 1, padding: '8px 0', background: viewMode === 'wiper' ? '#3B82F6' : '#222', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, cursor: 'pointer' }}>X光滑桿</button>
                  </div>

                  <h3 style={{ fontSize: 13, fontWeight: 600, color: '#aaa', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 16 }}>圖層控制 (Layers)</h3>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0', cursor: 'pointer' }}>
                    <input type="checkbox" checked={visibleLayers.added} onChange={(e) => setVisibleLayers(prev => ({ ...prev, added: e.target.checked }))} />
                    <div style={{ width: 12, height: 12, background: '#22c55e', borderRadius: 2 }} />
                    <span style={{ fontSize: 14 }}>新版模型 (New)</span>
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0', cursor: 'pointer' }}>
                    <input type="checkbox" checked={visibleLayers.removed} onChange={(e) => setVisibleLayers(prev => ({ ...prev, removed: e.target.checked }))} />
                    <div style={{ width: 12, height: 12, background: '#ef4444', borderRadius: 2 }} />
                    <span style={{ fontSize: 14 }}>舊版模型 (Old)</span>
                  </label>
                  
                  <div style={{ marginTop: 24, padding: 12, background: '#1a1a1a', borderRadius: 8, fontSize: 12, color: '#888', lineHeight: 1.5 }}>
                    <p style={{ marginBottom: 8 }}><strong>綠色</strong>代表新版模型。</p>
                    <p style={{ marginBottom: 8 }}><strong>紅色</strong>代表舊版模型。</p>
                    <p style={{ margin: 0 }}>目前為快速視覺疊圖，實際新增或移除區域需由工程師判讀。</p>
                  </div>
                </>
              )}

              {activeTab === 'data' && results?.stats && (
                <>
                  <h3 style={{ fontSize: 13, fontWeight: 600, color: '#aaa', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 16 }}>幾何數據差異報表</h3>
                  <div style={{ background: '#1a1a1a', borderRadius: 8, padding: 12, marginBottom: 16 }}>
                    <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>總體積變化 (Volume)</div>
                    <div style={{ fontSize: 16, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8 }}>
                      {results.stats.diff.volume > 0 ? <span style={{ color: '#22c55e' }}>+{results.stats.diff.volume.toFixed(2)} mm³</span> : <span style={{ color: '#ef4444' }}>{results.stats.diff.volume.toFixed(2)} mm³</span>}
                    </div>
                  </div>
                  <div style={{ background: '#1a1a1a', borderRadius: 8, padding: 12, marginBottom: 16 }}>
                    <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>總表面積變化 (Area)</div>
                    <div style={{ fontSize: 16, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8 }}>
                      {results.stats.diff.area > 0 ? <span style={{ color: '#22c55e' }}>+{results.stats.diff.area.toFixed(2)} mm²</span> : <span style={{ color: '#ef4444' }}>{results.stats.diff.area.toFixed(2)} mm²</span>}
                    </div>
                  </div>
                  <div style={{ background: '#1a1a1a', borderRadius: 8, padding: 12 }}>
                    <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>外觀長寬高 (Bounding Box)</div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 8, fontSize: 13 }}>
                      <span style={{ color: '#aaa' }}>X:</span>
                      <span>{results.stats.new.bbox[0].toFixed(2)} ({results.stats.diff.bbox[0] > 0 ? '+' : ''}{results.stats.diff.bbox[0].toFixed(2)})</span>
                      <span style={{ color: '#aaa' }}>Y:</span>
                      <span>{results.stats.new.bbox[1].toFixed(2)} ({results.stats.diff.bbox[1] > 0 ? '+' : ''}{results.stats.diff.bbox[1].toFixed(2)})</span>
                      <span style={{ color: '#aaa' }}>Z:</span>
                      <span>{results.stats.new.bbox[2].toFixed(2)} ({results.stats.diff.bbox[2] > 0 ? '+' : ''}{results.stats.diff.bbox[2].toFixed(2)})</span>
                    </div>
                  </div>
                </>
              )}

              {activeTab === 'tree' && (
                <div style={{ display: 'flex', gap: 16, height: '100%', overflow: 'hidden' }}>
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                    <h3 style={{ fontSize: 13, fontWeight: 600, color: '#aaa', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>舊版模型樹</h3>
                    <div 
                      ref={treeOldRef} 
                      onScroll={handleTreeOldScroll}
                      style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: 8, background: '#1a1a1a', borderRadius: 8, scrollBehavior: 'auto' }}
                    >
                      {diffedTreeOld ? <TreeNode node={diffedTreeOld} onSelect={()=>{}} selectedPart={null} /> : <div style={{ fontSize: 12, color: '#888' }}>無資料</div>}
                    </div>
                  </div>
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                    <h3 style={{ fontSize: 13, fontWeight: 600, color: '#aaa', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>新版模型樹</h3>
                    <div 
                      ref={treeNewRef} 
                      onScroll={handleTreeNewScroll}
                      style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: 8, background: '#1a1a1a', borderRadius: 8, scrollBehavior: 'auto' }}
                    >
                      {diffedTreeNew ? <TreeNode node={diffedTreeNew} onSelect={()=>{}} selectedPart={null} /> : <div style={{ fontSize: 12, color: '#888' }}>無資料</div>}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Resizer Handle */}
          <div 
            onMouseDown={startResizing}
            style={{ width: 4, cursor: 'col-resize', background: '#262626', zIndex: 10, transition: 'background 0.2s' }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = '#3B82F6'; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = '#262626'; }}
          />

          {/* Right Panel: 3D Canvas */}
          <div style={{ flex: 1, position: 'relative', background: '#0a0a0a' }}>
            <Canvas
              camera={{ position: [50, 50, 50], fov: 50, near: 0.01, far: 100000 }}
              gl={{ antialias: true, powerPreference: 'high-performance', failIfMajorPerformanceCaveat: false, localClippingEnabled: true }}
              onCreated={({ gl }) => {
                gl.setPixelRatio(Math.min(window.devicePixelRatio, 2));
              }}
            >
              <color attach="background" args={["#111111"]} />
              <ambientLight intensity={0.8} />
              <directionalLight position={[100, 100, 100]} intensity={1.5} />
              <directionalLight position={[-100, -50, -100]} intensity={0.5} />
              <pointLight position={[0, 100, 0]} intensity={0.5} />
              
              <DiffViewer diffUrls={diffUrls} visibleLayers={visibleLayers} viewMode={viewMode} wiperValue={wiperValue} />
              
              <OrbitControls makeDefault enableDamping dampingFactor={0.1} />
              <gridHelper args={[200, 20, '#333333', '#222222']} />
            </Canvas>
            
            {viewMode === 'wiper' && (
              <div style={{ position: 'absolute', bottom: 32, left: '50%', transform: 'translateX(-50%)', width: '60%', maxWidth: 400, background: 'rgba(0,0,0,0.7)', padding: '16px 24px', borderRadius: 12, backdropFilter: 'blur(10px)', border: '1px solid #333' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#aaa', marginBottom: 8, fontWeight: 600 }}>
                  <span style={{ color: '#ef4444' }}>舊版 (Old)</span>
                  <span style={{ color: '#22c55e' }}>新版 (New)</span>
                </div>
                <input 
                  type="range" 
                  min="0" max="100" 
                  value={wiperValue} 
                  onChange={e => setWiperValue(Number(e.target.value))} 
                  style={{ width: '100%', cursor: 'pointer' }} 
                />
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // --- Completed State ---
  const partsMap: Record<string, any> = results?.parts_map || {};
  const currentPartData = selectedPart ? partsMap[selectedPart] : null;

  // Determine the STL URL for the currently selected part
  const currentStlUrl = currentPartData?.stl ? `${API_BASE}${currentPartData.stl}` : null;
  const currentDrawingUrl = currentPartData?.pdf || currentPartData?.svg || currentPartData?.png;
  const frontViewUrl = currentPartData?.front_pdf || currentPartData?.front_svg;
  const backViewUrl = currentPartData?.back_pdf || currentPartData?.back_svg;
  const topViewUrl = currentPartData?.top_pdf || currentPartData?.top_svg;
  const rightViewUrl = currentPartData?.right_pdf || currentPartData?.right_svg;
  const leftViewUrl = currentPartData?.left_pdf || currentPartData?.left_svg;
  const featureLayerUrl = currentPartData?.features_pdf || currentPartData?.features_svg;
  const activeDrawingUrl = showFeatureLayer && featureLayerUrl ? featureLayerUrl : currentDrawingUrl;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#0A0A0A', color: '#fff' }}>
      {/* Header */}
      <header style={{ height: 56, borderBottom: '1px solid #262626', background: '#171717', display: 'flex', alignItems: 'center', padding: '0 16px', justifyContent: 'space-between', flexShrink: 0 }}>
        <div 
          onClick={() => { setStatus('idle'); setFile(null); setSelectedPart(null); }}
          style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', opacity: 0.9, transition: 'opacity 0.2s' }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.opacity = '1'; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.opacity = '0.9'; }}
        >
          <div style={{ width: 32, height: 32, background: '#3B82F6', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 14 }}>FC</div>
          <span style={{ fontWeight: 600, fontSize: 18, letterSpacing: 0.5 }}>Auto 2D Drawing System</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#888' }}>
          <CheckCircle size={16} color="#22c55e" />
          <span>處理完成 ({Object.keys(partsMap).length} 零件)</span>
          <button onClick={() => { setStatus('idle'); setFile(null); setSelectedPart(null); }} style={{ display: 'flex', alignItems: 'center', gap: 4, marginLeft: 16, padding: '6px 12px', background: '#333', border: 'none', borderRadius: 4, color: '#fff', cursor: 'pointer', fontSize: 13, transition: 'background 0.2s' }} onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = '#444'; }} onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = '#333'; }}>
            <Home size={14} /> 返回首頁
          </button>
        </div>
      </header>

      {/* Main Workspace */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* Left Panel: Tree View */}
        <div style={{ width: sidebarWidth, minWidth: 200, flexShrink: 0, background: '#111', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: 12, borderBottom: '1px solid #262626', fontSize: 11, fontWeight: 600, color: '#555', textTransform: 'uppercase', letterSpacing: 1 }}>
            模型目錄
          </div>
          <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: 8 }}>
            {results?.tree && (
              <TreeNode node={results.tree} onSelect={setSelectedPart} selectedPart={selectedPart} />
            )}
          </div>
        </div>

        {/* Resizer Handle */}
        <div 
          onMouseDown={startResizing}
          style={{ width: 4, background: '#262626', cursor: 'col-resize', zIndex: 10, transition: 'background 0.2s' }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = '#3B82F6'; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = '#262626'; }}
        />

        {/* Center Panel: 2D Viewer OR 3D Feature Space */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0A0A0A', borderRight: '1px solid #262626' }}>
          <div style={{ height: 40, borderBottom: '1px solid #262626', background: '#171717', display: 'flex', alignItems: 'center', padding: '0 16px', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {showFeatureLayer ? (
                <>
                  <Sparkles size={16} color="#38bdf8" />
                  <span style={{ fontSize: 13, fontWeight: 700, color: '#38bdf8' }}>3D 空間特徵標註 (3D Feature Space)</span>
                </>
              ) : (
                <span style={{ fontSize: 13, fontWeight: 500 }}>2D 工程圖</span>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              {currentDrawingUrl && (
                <button onClick={() => setShowFeatureLayer(false)} style={{ fontSize: 12, padding: '2px 8px', background: !showFeatureLayer ? '#3B82F6' : '#333', border: 'none', borderRadius: 4, color: '#fff', cursor: 'pointer' }}>合圖</button>
              )}
              {featureLayerUrl && (
                <button onClick={() => setShowFeatureLayer(true)} style={{ fontSize: 12, padding: '2px 8px', background: showFeatureLayer ? '#06b6d4' : '#333', border: 'none', borderRadius: 4, color: '#fff', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Layers size={13} />
                  <span>特徵圖層 (3D)</span>
                </button>
              )}
              {frontViewUrl && (
                <a href={`${API_BASE}${frontViewUrl}?t=${Date.now()}`} target="_blank" style={{ fontSize: 12, padding: '2px 8px', background: '#333', borderRadius: 4, color: '#ccc', textDecoration: 'none' }}>前視圖</a>
              )}
              {backViewUrl && (
                <a href={`${API_BASE}${backViewUrl}?t=${Date.now()}`} target="_blank" style={{ fontSize: 12, padding: '2px 8px', background: '#333', borderRadius: 4, color: '#ccc', textDecoration: 'none' }}>背面視圖</a>
              )}
              {topViewUrl && (
                <a href={`${API_BASE}${topViewUrl}?t=${Date.now()}`} target="_blank" style={{ fontSize: 12, padding: '2px 8px', background: '#333', borderRadius: 4, color: '#ccc', textDecoration: 'none' }}>俯視圖</a>
              )}
              {rightViewUrl && (
                <a href={`${API_BASE}${rightViewUrl}?t=${Date.now()}`} target="_blank" style={{ fontSize: 12, padding: '2px 8px', background: '#333', borderRadius: 4, color: '#ccc', textDecoration: 'none' }}>右側視圖</a>
              )}
              {leftViewUrl && (
                <a href={`${API_BASE}${leftViewUrl}?t=${Date.now()}`} target="_blank" style={{ fontSize: 12, padding: '2px 8px', background: '#333', borderRadius: 4, color: '#ccc', textDecoration: 'none' }}>左側視圖</a>
              )}
            </div>
          </div>

          <div style={{ flex: 1, display: 'flex', background: '#0f172a', position: 'relative' }}>
            {showFeatureLayer ? (
              currentStlUrl ? (
                <div style={{ flex: 1, position: 'relative', cursor: 'grab' }}>
                  <Canvas
                    key={`feature-3d-${currentStlUrl}`}
                    camera={{ position: [40, 40, 40], fov: 45, near: 0.01, far: 100000 }}
                    gl={{ antialias: true, powerPreference: 'high-performance' }}
                    onCreated={({ gl }) => {
                      gl.setPixelRatio(Math.min(window.devicePixelRatio, 2));
                    }}
                  >
                    <color attach="background" args={["#090d16"]} />
                    <ambientLight intensity={0.9} />
                    <directionalLight position={[100, 100, 100]} intensity={1.8} />
                    <directionalLight position={[-100, -50, -100]} intensity={0.7} />
                    <pointLight position={[0, 100, 0]} intensity={0.6} />
                    
                    <SinglePartViewerWithFeatures
                      stlUrl={currentStlUrl}
                      featureRecords={featureRecords}
                      selectedFeatureIds={selectedFeatureIds}
                      hoveredFeatureId={hoveredFeatureId}
                      focusedFeatureId={focusedFeatureId}
                      onHoverFeature={setHoveredFeatureId}
                      onSelectFeature={(id) => {
                        setFocusedFeatureId(id);
                        toggleFeature(id);
                      }}
                    />
                    
                    <OrbitControls makeDefault enableDamping dampingFactor={0.1} />
                    <gridHelper args={[200, 20, '#1e293b', '#0f172a']} />
                  </Canvas>

                  {/* 3D Navigation Hint */}
                  <div style={{ position: 'absolute', bottom: 12, left: 12, background: 'rgba(15, 23, 42, 0.85)', backdropFilter: 'blur(6px)', border: '1px solid #334155', borderRadius: 6, padding: '4px 10px', color: '#94a3b8', fontSize: 11, display: 'flex', alignItems: 'center', gap: 6, pointerEvents: 'none' }}>
                    <Sparkles size={13} color="#38bdf8" />
                    <span>左鍵旋轉 | 右鍵平移 | 滾輪縮放 | 點選標籤互動</span>
                  </div>
                </div>
              ) : (
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#64748b', flexDirection: 'column' }}>
                  <AlertTriangle size={36} style={{ marginBottom: 12, opacity: 0.4 }} />
                  <span style={{ fontSize: 13 }}>請從左側選擇零件以檢視 3D 特徵</span>
                </div>
              )
            ) : (
              activeDrawingUrl ? (
                <iframe
                  src={`${API_BASE}${activeDrawingUrl}?t=${Date.now()}#view=FitH`}
                  title="2D Drawing"
                  style={{ width: '100%', height: '100%', border: 'none' }}
                />
              ) : (
                <div style={{ flex: 1, display: 'flex', color: '#555', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
                  <File size={48} style={{ marginBottom: 16, opacity: 0.2 }} />
                  <span>從左側選擇零件查看工程圖</span>
                </div>
              )
            )}
          </div>
        </div>

        {/* Right Panel: Feature Inspector (in Feature Mode) OR Standard 3D Viewer (in 2D Mode) */}
        <div style={{ width: showFeatureLayer ? '38%' : '33%', minWidth: 320, display: 'flex', flexDirection: 'column', background: '#111', transition: 'width 0.2s ease' }}>
          {showFeatureLayer ? (() => {
            const filtered = featureRecords.filter(f => {
              const type = (f.type || '').toLowerCase();
              const role = (f.role || '').toLowerCase();
              let matchType = true;
              if (featureFilter === 'hole') matchType = type.includes('hole');
              else if (featureFilter === 'shaft') matchType = type.includes('shaft') || role.includes('journal') || type.includes('groove');
              else if (featureFilter === 'groove') matchType = type.includes('groove') || role.includes('groove') || role.includes('relief');
              else if (featureFilter === 'fillet') matchType = type.includes('fillet') || type.includes('round');
              else if (featureFilter === 'cone') matchType = type.includes('cone') || type.includes('chamfer') || role.includes('chamfer') || role.includes('pilot');
              else if (featureFilter === 'step') matchType = type.includes('step') || role.includes('step');
              else if (featureFilter === 'thickness') matchType = type.includes('thickness') || type.includes('plane') || type.includes('datum');
              else if (featureFilter === 'pattern') matchType = type.includes('pattern');
              else if (featureFilter === 'projected') matchType = type.includes('projected');
              
              if (!matchType) return false;
              if (!featureSearch) return true;
              const q = featureSearch.toLowerCase();
              return (
                (f.id && f.id.toLowerCase().includes(q)) ||
                (f.name && f.name.toLowerCase().includes(q)) ||
                (f.role && f.role.toLowerCase().includes(q)) ||
                (f.type && f.type.toLowerCase().includes(q)) ||
                (f.tolerance_key && f.tolerance_key.toLowerCase().includes(q)) ||
                (f.nominal && JSON.stringify(f.nominal).toLowerCase().includes(q))
              );
            });

            const allFilteredSelected = filtered.length > 0 && filtered.every(f => selectedFeatureIds.has(f.id));

            return (
              <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#0b1120' }}>
                {/* Inspector Header */}
                <div style={{ height: 44, borderBottom: '1px solid #1e293b', background: '#0f172a', display: 'flex', alignItems: 'center', padding: '0 14px', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 700, color: '#38bdf8' }}>
                    <Layers size={16} />
                    <span>3D 特徵管理面板</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 11, background: '#0284c7', color: '#fff', padding: '2px 8px', borderRadius: 10, fontWeight: 600 }}>
                      {selectedFeatureIds.size} / {featureRecords.length} 顯示
                    </span>
                  </div>
                </div>

                {/* Batch Action Bar (Select All / Deselect All) */}
                <div style={{ padding: '8px 12px', background: '#0f172a', borderBottom: '1px solid #1e293b', display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      onClick={() => selectAllFeatures(filtered.map(f => f.id))}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                        fontSize: 11,
                        padding: '4px 8px',
                        borderRadius: 4,
                        border: '1px solid #0284c7',
                        background: allFilteredSelected ? '#0284c7' : '#1e293b',
                        color: '#fff',
                        cursor: 'pointer',
                        fontWeight: 600,
                      }}
                    >
                      <CheckSquare size={13} />
                      <span>全選 ({filtered.length})</span>
                    </button>
                    <button
                      onClick={() => deselectAllFeatures(filtered.map(f => f.id))}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                        fontSize: 11,
                        padding: '4px 8px',
                        borderRadius: 4,
                        border: '1px solid #475569',
                        background: '#1e293b',
                        color: '#cbd5e1',
                        cursor: 'pointer',
                        fontWeight: 500,
                      }}
                    >
                      <Square size={13} />
                      <span>取消全選</span>
                    </button>
                  </div>
                  <span style={{ fontSize: 11, color: '#64748b' }}>
                    已篩選: {filtered.length} 筆
                  </span>
                </div>

                {/* Search Input */}
                <div style={{ padding: '8px 12px', background: '#0b1120' }}>
                  <div style={{ position: 'relative' }}>
                    <input
                      type="text"
                      value={featureSearch}
                      onChange={(e) => setFeatureSearch(e.target.value)}
                      placeholder="搜尋特徵名稱、ID、直徑、尺寸、卡簧槽..."
                      style={{ width: '100%', boxSizing: 'border-box', padding: '6px 10px', background: '#0f172a', border: '1px solid #334155', borderRadius: 6, color: '#f8fafc', fontSize: 11 }}
                    />
                  </div>
                </div>

                {/* Category Filter Pills */}
                <div style={{ padding: '0 12px 8px 12px', display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {[
                    { id: 'ALL', label: '全部' },
                    { id: 'shaft', label: '軸/配合段' },
                    { id: 'groove', label: '卡簧/凹槽' },
                    { id: 'cone', label: '倒角/錐面' },
                    { id: 'step', label: '階梯/段差' },
                    { id: 'fillet', label: '圓角' },
                    { id: 'hole', label: '孔洞' },
                    { id: 'thickness', label: '壁厚/基準' },
                    { id: 'pattern', label: '孔群' },
                    { id: 'projected', label: '2D投影' },
                  ].map(tab => (
                    <button
                      key={tab.id}
                      onClick={() => setFeatureFilter(tab.id)}
                      style={{
                        fontSize: 10,
                        padding: '2px 7px',
                        borderRadius: 4,
                        border: 'none',
                        cursor: 'pointer',
                        background: featureFilter === tab.id ? '#0284c7' : '#1e293b',
                        color: featureFilter === tab.id ? '#fff' : '#94a3b8',
                        fontWeight: featureFilter === tab.id ? 700 : 500
                      }}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {/* Feature Items Scrollable List */}
                <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 12px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {filtered.map((feature, idx) => {
                    const isSelected = selectedFeatureIds.has(feature.id);
                    const isHovered = hoveredFeatureId === feature.id;
                    const fType = (feature.type || '').toLowerCase();

                    let badgeBg = '#334155';
                    let badgeColor = '#94a3b8';
                    if (fType.includes('journal') || fType.includes('shaft')) { badgeBg = '#1e3a8a'; badgeColor = '#60a5fa'; }
                    else if (fType.includes('groove') || fType.includes('slot')) { badgeBg = '#581c87'; badgeColor = '#c084fc'; }
                    else if (fType.includes('cone') || fType.includes('chamfer')) { badgeBg = '#713f12'; badgeColor = '#facc15'; }
                    else if (fType.includes('step')) { badgeBg = '#7c2d12'; badgeColor = '#fb923c'; }
                    else if (fType.includes('fillet')) { badgeBg = '#831843'; badgeColor = '#f472b6'; }
                    else if (fType.includes('hole')) { badgeBg = '#065f46'; badgeColor = '#34d399'; }
                    else if (fType.includes('thickness') || fType.includes('plane')) { badgeBg = '#374151'; badgeColor = '#9ca3af'; }
                    else if (fType.includes('pattern')) { badgeBg = '#4c1d95'; badgeColor = '#e9d5ff'; }
                    else if (fType.includes('projected')) { badgeBg = '#155e75'; badgeColor = '#22d3ee'; }

                    return (
                      <div
                        key={feature.id || idx}
                        onMouseEnter={() => setHoveredFeatureId(feature.id)}
                        onMouseLeave={() => setHoveredFeatureId(null)}
                        style={{
                          padding: '8px 10px',
                          background: isHovered ? '#1e293b' : '#0f172a',
                          borderRadius: 6,
                          border: `1px solid ${isHovered ? '#38bdf8' : isSelected ? '#1e3a8a' : '#1e293b'}`,
                          transition: 'all 0.15s ease',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 4,
                          boxShadow: isHovered ? '0 4px 12px rgba(0,0,0,0.4)' : 'none',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', flex: 1 }}>
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleFeature(feature.id)}
                              style={{ cursor: 'pointer', accentColor: '#0284c7', width: 15, height: 15 }}
                            />
                            <span style={{ fontWeight: 700, color: isSelected ? '#f8fafc' : '#64748b', fontSize: 12 }}>
                              {feature.id || `F${idx + 1}`}
                            </span>
                          </label>
                          <span style={{ fontSize: 9, padding: '2px 6px', borderRadius: 3, background: badgeBg, color: badgeColor, fontWeight: 600 }}>
                            {feature.type}
                          </span>
                        </div>

                        <div style={{ fontWeight: 600, color: isSelected ? '#38bdf8' : '#94a3b8', fontSize: 12, paddingLeft: 23 }}>
                          {feature.name}
                        </div>

                        <div style={{ color: '#64748b', fontSize: 10, display: 'flex', gap: 6, paddingLeft: 23 }}>
                          <span>角色: {feature.role}</span>
                          <span>•</span>
                          <span>公差: {feature.tolerance_key}</span>
                        </div>

                        {feature.nominal && Object.keys(feature.nominal).length > 0 && (
                          <div style={{ marginLeft: 23, color: '#94a3b8', fontSize: 10, background: 'rgba(0,0,0,0.4)', padding: '3px 6px', borderRadius: 4, fontFamily: 'monospace' }}>
                            {Object.entries(feature.nominal).map(([k, v]) => `${k}: ${typeof v === 'number' ? (v as number).toFixed(2) : (typeof v === 'object' ? JSON.stringify(v) : v)}`).join(' | ')}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })() : (
            <>
              <div style={{ height: 40, borderBottom: '1px solid #262626', background: '#171717', display: 'flex', alignItems: 'center', padding: '0 16px', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 13, fontWeight: 500 }}>3D 互動檢視</span>
                <span style={{ fontSize: 11, color: '#555' }}>選中的零件</span>
              </div>
              <div style={{ flex: 1, position: 'relative', cursor: 'move' }}>
                {currentStlUrl ? (
                  <Canvas
                    key={currentStlUrl}
                    camera={{ position: [50, 50, 50], fov: 50, near: 0.01, far: 100000 }}
                    gl={{ antialias: true, powerPreference: 'high-performance', failIfMajorPerformanceCaveat: false }}
                    onCreated={({ gl }) => {
                      gl.setPixelRatio(Math.min(window.devicePixelRatio, 2));
                    }}
                  >
                    <color attach="background" args={["#111111"]} />
                    <ambientLight intensity={0.8} />
                    <directionalLight position={[100, 100, 100]} intensity={1.5} />
                    <directionalLight position={[-100, -50, -100]} intensity={0.5} />
                    <pointLight position={[0, 100, 0]} intensity={0.5} />
                    <SinglePartViewer stlUrl={currentStlUrl} />
                    <OrbitControls makeDefault enableDamping dampingFactor={0.1} />
                    <gridHelper args={[200, 20, '#333333', '#222222']} />
                  </Canvas>
                ) : (
                  <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555', flexDirection: 'column' }}>
                    <AlertTriangle size={32} style={{ marginBottom: 12, opacity: 0.3 }} />
                    <span style={{ fontSize: 13 }}>選擇零件以載入 3D 模型</span>
                  </div>
                )}
              </div>
            </>
          )}
        </div>

      </div>
    </div>
  );
}

export default App;
