import React, { useState, useEffect, useRef } from 'react';
import { 
  FileText, File, Folder, FolderOpen, Loader2, CheckCircle, 
  ChevronRight, ChevronDown, AlertTriangle, BookOpen, ArrowLeft, 
  Home, ZoomIn, ZoomOut, CheckSquare, Square, 
  Layers, Sparkles, Wand2, Download, Save, Trash2
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
  viewMode,
  onHoverFeature, 
  onSelectFeature,
  onModelLoaded,
}: {
  stlUrl: string;
  featureRecords: any[];
  selectedFeatureIds: Set<string>;
  hoveredFeatureId: string | null;
  focusedFeatureId?: string | null;
  viewMode?: { type: string; ts: number } | null;
  onHoverFeature: (id: string | null) => void;
  onSelectFeature: (id: string) => void;
  onModelLoaded?: (radius: number) => void;
}) {
  const [geometry, setGeometry] = useState<THREE.BufferGeometry | null>(null);
  const [modelCenter, setModelCenter] = useState<THREE.Vector3>(new THREE.Vector3(0, 0, 0));
  const [modelRadius, setModelRadius] = useState<number>(20);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { camera, controls } = useThree();

  const applyView = (type: string, radius: number) => {
    const r = Math.max(1.0, radius);
    const dist = r * 2.2;
    const target = new THREE.Vector3(0, 0, 0);

    if (type === 'front') {
      camera.position.set(0, 0, dist * 1.5);
    } else if (type === 'top') {
      camera.position.set(0, dist * 1.5, 0.0001);
    } else if (type === 'right') {
      camera.position.set(dist * 1.5, 0, 0);
    } else if (type === 'iso' || type === 'fit') {
      camera.position.set(dist, dist * 0.75, dist);
    }

    (camera as THREE.PerspectiveCamera).near = Math.max(0.001, r * 0.01);
    (camera as THREE.PerspectiveCamera).far = Math.max(1000, r * 200);
    camera.lookAt(target);
    camera.updateProjectionMatrix();

    if (controls) {
      (controls as any).target.copy(target);
      (controls as any).update();
    }
  };

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
        const radius = Math.max(1.0, sphere.radius);
        setModelRadius(radius);
        if (onModelLoaded) onModelLoaded(radius);

        applyView('iso', radius);

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

  useEffect(() => {
    if (viewMode && modelRadius && geometry) {
      applyView(viewMode.type, modelRadius);
    }
  }, [viewMode]);

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
                emissiveIntensity={isHovered ? 0.9 : 0.15}
                transparent={true}
                opacity={isHovered ? 0.55 : 0.08}
                roughness={0.2}
                depthWrite={false}
              />
            </mesh>

            {/* Glowing 3D Center Marker Pin */}
            <mesh position={[0, rawSize[1] / 2 + 0.1, 0]}>
              <sphereGeometry args={[isHovered ? 0.25 : 0.12, 16, 16]} />
              <meshStandardMaterial
                color={boxColor}
                emissive={boxColor}
                emissiveIntensity={isHovered ? 1.8 : 0.6}
                roughness={0.2}
              />
            </mesh>

            {/* 3D Floating HTML Label Pin (僅在 Hover、聚焦或特徵數量 <= 8 時渲染，避免畫面過度遮擋) */}
            {(isHovered || focusedFeatureId === feat.id || selectedFeatureIds.size <= 8) && (
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
                  zIndex: isHovered ? 99 : 1,
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
            )}
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
  const [feature3DViewMode, setFeature3DViewMode] = useState<{ type: string; ts: number } | null>(null);
  const [feature3DModelRadius, setFeature3DModelRadius] = useState<number>(20);

  // --- Smart Annotation Studio State ---
  const [viewTab, setViewTab] = useState<'main' | 'features3d' | 'smart_annotation'>('main');
  const [annotationTemplates, setAnnotationTemplates] = useState<any[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>('general_mechanical_preset');
  const [annotationConfig, setAnnotationConfig] = useState<Record<string, {
    enabled?: boolean;
    preferred_view?: string;
    tolerance?: string;
    side?: string;
    baseline?: string;
  }>>({});
  const [customDrawingResult, setCustomDrawingResult] = useState<{
    dxf_url?: string;
    pdf_url?: string;
    png_url?: string;
    timestamp?: string;
  } | null>(null);
  const [isRenderingDrawing, setIsRenderingDrawing] = useState(false);
  const [saveTemplateModalOpen, setSaveTemplateModalOpen] = useState(false);
  const [newTemplateName, setNewTemplateName] = useState('');
  const [newTemplateDesc, setNewTemplateDesc] = useState('');
  const [annotationZoom, setAnnotationZoom] = useState(1);

  // --- Tree Diff State ---
  const [diffedTreeOld, setDiffedTreeOld] = useState<any>(null);
  const [diffedTreeNew, setDiffedTreeNew] = useState<any>(null);

  useEffect(() => {
    axios.get(`${API_BASE}/api/annotation/templates`)
      .then(res => {
        if (Array.isArray(res.data?.templates)) {
          setAnnotationTemplates(res.data.templates);
          if (res.data.templates.length > 0) {
            setSelectedTemplateId(res.data.templates[0].id);
          }
        }
      })
      .catch(err => console.error('Failed to load annotation templates:', err));
  }, []);

  useEffect(() => {
    setCustomDrawingResult(null);
  }, [selectedPart]);

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

    const modelId = results?.model_id || results?.output_dir || jobId;
    const partsMapForFeatures: Record<string, any> = results?.parts_map || {};
    const selectedData = selectedPart ? partsMapForFeatures[selectedPart] : null;

    if (!selectedPart) return;

    // 優先調用後端即時 3D 特徵動態提取 API (保證 100% 所有零件/新舊模型皆有 3D 特徵)
    if (modelId) {
      axios.get(`${API_BASE}/api/features/${modelId}/${selectedPart}?t=${Date.now()}`)
        .then(res => {
          const records = Array.isArray(res.data?.records) ? res.data.records : [];
          if (records.length > 0) {
            setFeatureRecords(records);
            const initialSelected = records.length <= 15
              ? records.map((r: any) => r.id)
              : records.slice(0, 12).map((r: any) => r.id);
            setSelectedFeatureIds(new Set(initialSelected));
            return;
          }
          if (selectedData?.features_json) {
            axios.get(`${API_BASE}${selectedData.features_json}?t=${Date.now()}`)
              .then(res2 => {
                const recs = Array.isArray(res2.data) ? res2.data : [];
                setFeatureRecords(recs);
                const initial = recs.length <= 15
                  ? recs.map((r: any) => r.id)
                  : recs.slice(0, 12).map((r: any) => r.id);
                setSelectedFeatureIds(new Set(initial));
              })
              .catch(() => {});
          }
        })
        .catch(err => {
          console.error('Dynamic feature API error:', err);
          if (selectedData?.features_json) {
            axios.get(`${API_BASE}${selectedData.features_json}?t=${Date.now()}`)
              .then(res2 => {
                const recs = Array.isArray(res2.data) ? res2.data : [];
                setFeatureRecords(recs);
                const initial = recs.length <= 15
                  ? recs.map((r: any) => r.id)
                  : recs.slice(0, 12).map((r: any) => r.id);
                setSelectedFeatureIds(new Set(initial));
              })
              .catch(() => {
                setFeatureRecords([]);
                setSelectedFeatureIds(new Set());
              });
          }
        });
    } else if (selectedData?.features_json) {
      axios.get(`${API_BASE}${selectedData.features_json}?t=${Date.now()}`)
        .then(res => {
          const records = Array.isArray(res.data) ? res.data : [];
          setFeatureRecords(records);
          const initialSelected = records.length <= 15
            ? records.map((r: any) => r.id)
            : records.slice(0, 12).map((r: any) => r.id);
          setSelectedFeatureIds(new Set(initialSelected));
        })
        .catch(err => {
          console.error('Feature records load error:', err);
          setFeatureRecords([]);
          setSelectedFeatureIds(new Set());
        });
    }
  }, [results, selectedPart, jobId]);

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

  const updateFeatureConfig = (id: string, updates: Partial<{
    enabled: boolean;
    preferred_view: string;
    tolerance: string;
    side: string;
    baseline: string;
  }>) => {
    setAnnotationConfig(prev => ({
      ...prev,
      [id]: {
        ...(prev[id] || {}),
        ...updates
      }
    }));
    if (updates.enabled !== undefined) {
      setSelectedFeatureIds(prev => {
        const next = new Set(prev);
        if (updates.enabled) next.add(id);
        else next.delete(id);
        return next;
      });
    }
  };

  const handleApplyTemplate = (templateId: string) => {
    if (!templateId || featureRecords.length === 0) return;
    axios.post(`${API_BASE}/api/annotation/apply-template`, {
      template_id: templateId,
      feature_records: featureRecords
    })
      .then(res => {
        if (res.data?.records) {
          const updated = res.data.records;
          setFeatureRecords(updated);
          const newConfig: Record<string, any> = {};
          const newSelectedIds = new Set<string>();
          updated.forEach((r: any) => {
            newConfig[r.id] = {
              enabled: !!r.enabled,
              preferred_view: r.preferred_view || 'front',
              tolerance: r.tolerance || '',
              side: r.side || 'BOTTOM',
              baseline: r.baseline || 'NONE'
            };
            if (r.enabled) newSelectedIds.add(r.id);
          });
          setAnnotationConfig(newConfig);
          setSelectedFeatureIds(newSelectedIds);
        }
      })
      .catch(err => console.error('Error applying template:', err));
  };

  const handleRenderCustomDrawing = async () => {
    const modelId = results?.model_id || results?.output_dir || jobId;
    if (!modelId || !selectedPart) {
      alert('請先從左側選擇零件！');
      return;
    }

    setIsRenderingDrawing(true);
    try {
      const payloadRecords = featureRecords.map(f => {
        const cfg = annotationConfig[f.id] || {};
        return {
          ...f,
          enabled: selectedFeatureIds.has(f.id) || !!cfg.enabled,
          preferred_view: cfg.preferred_view || f.preferred_view || 'front',
          tolerance: cfg.tolerance !== undefined ? cfg.tolerance : (f.tolerance || ''),
          side: cfg.side || f.side || 'BOTTOM',
          baseline: cfg.baseline || f.baseline || 'NONE'
        };
      });

      const res = await axios.post(`${API_BASE}/api/annotation/render`, {
        model_id: modelId,
        part_id: selectedPart,
        feature_records: payloadRecords,
        title_info: {
          part_name: `${selectedPart} (SMART ANNOTATED)`,
          drawing_no: `DWG-${selectedPart}-001`,
          model_code: modelId
        }
      });

      if (res.data?.status === 'ok') {
        setCustomDrawingResult({
          dxf_url: res.data.dxf_url,
          pdf_url: res.data.pdf_url,
          png_url: res.data.png_url,
          timestamp: res.data.timestamp
        });
      }
    } catch (err: any) {
      console.error('Render custom drawing error:', err);
      alert(`生成工程圖失敗: ${err?.response?.data?.detail || err.message}`);
    } finally {
      setIsRenderingDrawing(false);
    }
  };

  const handleSaveNewTemplate = async () => {
    if (!newTemplateName.trim()) return;

    const rules: any[] = [];
    featureRecords.forEach(f => {
      if (selectedFeatureIds.has(f.id)) {
        const cfg = annotationConfig[f.id] || {};
        rules.push({
          feature_type: f.type,
          role_pattern: f.role ? `^${f.role}$` : undefined,
          enabled: true,
          preferred_view: cfg.preferred_view || f.preferred_view || 'front',
          tolerance: cfg.tolerance || '',
          side: cfg.side || f.side || 'BOTTOM',
          baseline: cfg.baseline || 'NONE',
          rank: 1
        });
      }
    });

    try {
      const res = await axios.post(`${API_BASE}/api/annotation/templates`, {
        name: newTemplateName.trim(),
        description: newTemplateDesc.trim() || `自訂標註樣板 (${new Date().toLocaleDateString()})`,
        target_type: 'CUSTOM',
        rules: rules
      });

      if (res.data?.template) {
        setAnnotationTemplates(prev => [...prev, res.data.template]);
        setSelectedTemplateId(res.data.template.id);
        setSaveTemplateModalOpen(false);
        setNewTemplateName('');
        setNewTemplateDesc('');
        alert(`樣板「${res.data.template.name}」已儲存！`);
      }
    } catch (err: any) {
      alert(`儲存樣板失敗: ${err.message}`);
    }
  };

  const handleDeleteTemplate = async (templateId: string) => {
    if (!confirm('確定要刪除此樣板嗎？')) return;
    try {
      await axios.delete(`${API_BASE}/api/annotation/templates/${templateId}`);
      setAnnotationTemplates(prev => prev.filter(t => t.id !== templateId));
      setSelectedTemplateId('general_mechanical_preset');
    } catch (err: any) {
      alert(`刪除樣板失敗: ${err?.response?.data?.detail || err.message}`);
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

        {/* Center Panel: 2D Viewer OR 3D Feature Space OR Smart Annotation Studio */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0A0A0A', borderRight: '1px solid #262626' }}>
          <div style={{ height: 44, borderBottom: '1px solid #262626', background: '#171717', display: 'flex', alignItems: 'center', padding: '0 16px', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {viewTab === 'smart_annotation' ? (
                <>
                  <Wand2 size={16} color="#c084fc" />
                  <span style={{ fontSize: 13, fontWeight: 700, background: 'linear-gradient(90deg, #c084fc, #38bdf8)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                    ✨ 智慧特徵標註工作室 (Smart Annotation Studio)
                  </span>
                </>
              ) : viewTab === 'features3d' ? (
                <>
                  <Sparkles size={16} color="#38bdf8" />
                  <span style={{ fontSize: 13, fontWeight: 700, color: '#38bdf8' }}>3D 空間特徵標註 (3D Feature Space)</span>
                </>
              ) : (
                <span style={{ fontSize: 13, fontWeight: 500 }}>2D 工程圖</span>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {currentDrawingUrl && (
                <button
                  onClick={() => { setViewTab('main'); setShowFeatureLayer(false); }}
                  style={{
                    fontSize: 12,
                    padding: '3px 10px',
                    background: viewTab === 'main' ? '#3B82F6' : '#262626',
                    border: 'none',
                    borderRadius: 4,
                    color: '#fff',
                    cursor: 'pointer',
                    fontWeight: viewTab === 'main' ? 700 : 500,
                  }}
                >
                  合圖
                </button>
              )}
              {featureLayerUrl && (
                <button
                  onClick={() => { setViewTab('features3d'); setShowFeatureLayer(true); }}
                  style={{
                    fontSize: 12,
                    padding: '3px 10px',
                    background: viewTab === 'features3d' ? '#06b6d4' : '#262626',
                    border: 'none',
                    borderRadius: 4,
                    color: '#fff',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4,
                    fontWeight: viewTab === 'features3d' ? 700 : 500,
                  }}
                >
                  <Layers size={13} />
                  <span>特徵圖層 (3D)</span>
                </button>
              )}
              {/* ✨ 新版智慧標註獨立分頁按鈕 */}
              <button
                onClick={() => { setViewTab('smart_annotation'); setShowFeatureLayer(false); }}
                style={{
                  fontSize: 12,
                  padding: '3px 12px',
                  background: viewTab === 'smart_annotation'
                    ? 'linear-gradient(135deg, #7e22ce, #2563eb)'
                    : '#1e1b4b',
                  border: `1px solid ${viewTab === 'smart_annotation' ? '#c084fc' : '#4338ca'}`,
                  borderRadius: 4,
                  color: '#fff',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  fontWeight: 700,
                  boxShadow: viewTab === 'smart_annotation' ? '0 0 12px rgba(192, 132, 252, 0.4)' : 'none',
                  transition: 'all 0.2s ease',
                }}
              >
                <Wand2 size={13} color="#f472b6" />
                <span>✨ 智慧特徵標註</span>
              </button>

              <span style={{ width: 1, height: 16, background: '#333', margin: '0 4px' }} />

              {frontViewUrl && (
                <a href={`${API_BASE}${frontViewUrl}?t=${Date.now()}`} target="_blank" rel="noreferrer" style={{ fontSize: 12, padding: '2px 8px', background: '#333', borderRadius: 4, color: '#ccc', textDecoration: 'none' }}>前視圖</a>
              )}
              {backViewUrl && (
                <a href={`${API_BASE}${backViewUrl}?t=${Date.now()}`} target="_blank" rel="noreferrer" style={{ fontSize: 12, padding: '2px 8px', background: '#333', borderRadius: 4, color: '#ccc', textDecoration: 'none' }}>背面視圖</a>
              )}
              {topViewUrl && (
                <a href={`${API_BASE}${topViewUrl}?t=${Date.now()}`} target="_blank" rel="noreferrer" style={{ fontSize: 12, padding: '2px 8px', background: '#333', borderRadius: 4, color: '#ccc', textDecoration: 'none' }}>俯視圖</a>
              )}
              {rightViewUrl && (
                <a href={`${API_BASE}${rightViewUrl}?t=${Date.now()}`} target="_blank" rel="noreferrer" style={{ fontSize: 12, padding: '2px 8px', background: '#333', borderRadius: 4, color: '#ccc', textDecoration: 'none' }}>右側視圖</a>
              )}
              {leftViewUrl && (
                <a href={`${API_BASE}${leftViewUrl}?t=${Date.now()}`} target="_blank" rel="noreferrer" style={{ fontSize: 12, padding: '2px 8px', background: '#333', borderRadius: 4, color: '#ccc', textDecoration: 'none' }}>左側視圖</a>
              )}
            </div>
          </div>

          {/* Center Main Display Area */}
          <div style={{ flex: 1, display: 'flex', background: '#0f172a', position: 'relative', overflow: 'hidden' }}>
            {viewTab === 'smart_annotation' ? (
              customDrawingResult?.png_url ? (
                <div style={{ flex: 1, position: 'relative', display: 'flex', flexDirection: 'column', height: '100%', background: '#090d16' }}>
                  {/* Custom Drawing Action Toolbar */}
                  <div style={{ padding: '8px 16px', background: '#0f172a', borderBottom: '1px solid #1e293b', display: 'flex', justifyContent: 'space-between', alignItems: 'center', zIndex: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <span style={{ fontSize: 12, fontWeight: 700, color: '#38bdf8' }}>
                        🎯 客製工程圖 ({selectedPart})
                      </span>
                      <span style={{ fontSize: 11, background: '#166534', color: '#86efac', padding: '2px 8px', borderRadius: 10, fontWeight: 600 }}>
                        已套用 {selectedFeatureIds.size} 處特徵標註
                      </span>
                    </div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      {/* Zoom Controls */}
                      <div style={{ display: 'flex', gap: 4, background: '#1e293b', padding: '2px 6px', borderRadius: 6, border: '1px solid #334155' }}>
                        <button onClick={() => setAnnotationZoom(z => Math.max(0.4, z - 0.2))} style={{ background: 'transparent', border: 'none', color: '#cbd5e1', cursor: 'pointer', padding: 4 }}><ZoomOut size={14} /></button>
                        <span style={{ fontSize: 11, color: '#94a3b8', minWidth: 36, textAlign: 'center', lineHeight: '22px' }}>{Math.round(annotationZoom * 100)}%</span>
                        <button onClick={() => setAnnotationZoom(z => Math.min(3.0, z + 0.2))} style={{ background: 'transparent', border: 'none', color: '#cbd5e1', cursor: 'pointer', padding: 4 }}><ZoomIn size={14} /></button>
                        <button onClick={() => setAnnotationZoom(1)} style={{ background: 'transparent', border: 'none', color: '#38bdf8', cursor: 'pointer', fontSize: 10, fontWeight: 700, padding: '0 4px' }}>1x</button>
                      </div>

                      {/* Download Actions */}
                      {customDrawingResult.dxf_url && (
                        <a
                          href={`${API_BASE}${customDrawingResult.dxf_url}`}
                          download
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 4,
                            fontSize: 11,
                            padding: '4px 10px',
                            background: '#0284c7',
                            color: '#fff',
                            borderRadius: 4,
                            textDecoration: 'none',
                            fontWeight: 600,
                          }}
                        >
                          <Download size={13} />
                          <span>下載 DXF</span>
                        </a>
                      )}
                      {customDrawingResult.pdf_url && (
                        <a
                          href={`${API_BASE}${customDrawingResult.pdf_url}`}
                          target="_blank"
                          rel="noreferrer"
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 4,
                            fontSize: 11,
                            padding: '4px 10px',
                            background: '#7c3aed',
                            color: '#fff',
                            borderRadius: 4,
                            textDecoration: 'none',
                            fontWeight: 600,
                          }}
                        >
                          <FileText size={13} />
                          <span>檢視 PDF</span>
                        </a>
                      )}
                    </div>
                  </div>

                  {/* High-Res Drawing Image Viewport */}
                  <div style={{ flex: 1, overflow: 'auto', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
                    <img
                      src={`${API_BASE}${customDrawingResult.png_url}?t=${customDrawingResult.timestamp || Date.now()}`}
                      alt="Custom Drawing"
                      style={{
                        width: `${100 * annotationZoom}%`,
                        maxWidth: 'none',
                        maxHeight: 'none',
                        transition: 'width 0.15s ease',
                        boxShadow: '0 12px 40px rgba(0,0,0,0.6)',
                        borderRadius: 6,
                        border: '1px solid #1e293b',
                        background: '#000',
                      }}
                    />
                  </div>
                </div>
              ) : (
                <div style={{ flex: 1, position: 'relative', display: 'flex', flexDirection: 'column', height: '100%', background: '#090d16' }}>
                  {/* Studio Hero State with 3D Preview */}
                  <div style={{ padding: '10px 16px', background: 'linear-gradient(90deg, rgba(88, 28, 135, 0.3), rgba(30, 58, 138, 0.3))', borderBottom: '1px solid #334155', display: 'flex', alignItems: 'center', justifyContent: 'space-between', zIndex: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <Wand2 size={16} color="#c084fc" />
                      <span style={{ fontSize: 13, fontWeight: 700, color: '#f8fafc' }}>
                        智慧標註工作室 — 請在右側勾選欲標註特徵、自訂公差或一鍵套用樣板
                      </span>
                    </div>
                    <span style={{ fontSize: 11, color: '#94a3b8' }}>
                      零件: <strong style={{ color: '#38bdf8' }}>{selectedPart || '未選取'}</strong>
                    </span>
                  </div>

                  <div style={{ flex: 1, position: 'relative' }}>
                    {currentStlUrl ? (
                      <Canvas
                        key={`studio-3d-${currentStlUrl}`}
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
                          viewMode={feature3DViewMode}
                          onHoverFeature={setHoveredFeatureId}
                          onSelectFeature={(id) => {
                            setFocusedFeatureId(id);
                            toggleFeature(id);
                          }}
                          onModelLoaded={setFeature3DModelRadius}
                        />

                        <OrbitControls makeDefault enableDamping dampingFactor={0.1} />
                        <gridHelper args={[Math.max(20, Math.ceil(feature3DModelRadius * 2.5)), 20, '#1e293b', '#0f172a']} />
                      </Canvas>
                    ) : (
                      <div style={{ flex: 1, height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#64748b', flexDirection: 'column' }}>
                        <AlertTriangle size={36} style={{ marginBottom: 12, opacity: 0.4 }} />
                        <span style={{ fontSize: 13 }}>請從左側目錄選擇零件</span>
                      </div>
                    )}

                    {/* Quick View Controls */}
                    <div style={{ position: 'absolute', top: 12, right: 12, display: 'flex', gap: 6, zIndex: 10 }}>
                      {[
                        { key: 'iso', label: '等角 ISO' },
                        { key: 'top', label: '俯視 Top' },
                        { key: 'front', label: '正視 Front' },
                        { key: 'right', label: '側視 Right' },
                        { key: 'fit', label: '置中 Fit' },
                      ].map(v => (
                        <button
                          key={v.key}
                          onClick={() => setFeature3DViewMode({ type: v.key, ts: Date.now() })}
                          style={{
                            background: 'rgba(15, 23, 42, 0.85)',
                            backdropFilter: 'blur(6px)',
                            border: '1px solid #334155',
                            borderRadius: 6,
                            padding: '4px 9px',
                            color: '#cbd5e1',
                            fontSize: 11,
                            cursor: 'pointer',
                            fontWeight: 600,
                          }}
                        >
                          {v.label}
                        </button>
                      ))}
                    </div>

                    {/* Floating Callout Button */}
                    <div style={{ position: 'absolute', bottom: 20, left: '50%', transform: 'translateX(-50%)', background: 'rgba(15, 23, 42, 0.92)', backdropFilter: 'blur(10px)', border: '1px solid #3b82f6', borderRadius: 10, padding: '10px 20px', display: 'flex', alignItems: 'center', gap: 14, boxShadow: '0 8px 24px rgba(0,0,0,0.5)', zIndex: 10 }}>
                      <Sparkles size={18} color="#38bdf8" />
                      <span style={{ fontSize: 13, color: '#e2e8f0' }}>
                        已選取 <strong style={{ color: '#38bdf8' }}>{selectedFeatureIds.size}</strong> 處特徵，準備產出專屬工程圖
                      </span>
                      <button
                        onClick={handleRenderCustomDrawing}
                        disabled={isRenderingDrawing || selectedFeatureIds.size === 0}
                        style={{
                          background: selectedFeatureIds.size > 0 ? 'linear-gradient(135deg, #0284c7, #2563eb)' : '#334155',
                          color: '#fff',
                          border: 'none',
                          borderRadius: 6,
                          padding: '6px 14px',
                          fontSize: 12,
                          fontWeight: 700,
                          cursor: selectedFeatureIds.size > 0 ? 'pointer' : 'not-allowed',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 6,
                          boxShadow: selectedFeatureIds.size > 0 ? '0 4px 12px rgba(2, 132, 199, 0.4)' : 'none',
                        }}
                      >
                        {isRenderingDrawing ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />}
                        <span>{isRenderingDrawing ? '生成中...' : '立即生成客製圖紙'}</span>
                      </button>
                    </div>
                  </div>
                </div>
              )
            ) : viewTab === 'features3d' ? (
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
                      viewMode={feature3DViewMode}
                      onHoverFeature={setHoveredFeatureId}
                      onSelectFeature={(id) => {
                        setFocusedFeatureId(id);
                        toggleFeature(id);
                      }}
                      onModelLoaded={setFeature3DModelRadius}
                    />

                    <OrbitControls makeDefault enableDamping dampingFactor={0.1} />
                    <gridHelper args={[Math.max(20, Math.ceil(feature3DModelRadius * 2.5)), 20, '#1e293b', '#0f172a']} />
                  </Canvas>

                  {/* 3D Quick View Toolbar */}
                  <div style={{ position: 'absolute', top: 12, right: 12, display: 'flex', gap: 6, zIndex: 10 }}>
                    {[
                      { key: 'iso', label: '等角 ISO' },
                      { key: 'top', label: '俯視 Top' },
                      { key: 'front', label: '正視 Front' },
                      { key: 'right', label: '側視 Right' },
                      { key: 'fit', label: '自適應置中 Fit' },
                    ].map(v => (
                      <button
                        key={v.key}
                        onClick={() => setFeature3DViewMode({ type: v.key, ts: Date.now() })}
                        style={{
                          background: 'rgba(15, 23, 42, 0.85)',
                          backdropFilter: 'blur(6px)',
                          border: '1px solid #334155',
                          borderRadius: 6,
                          padding: '4px 9px',
                          color: '#cbd5e1',
                          fontSize: 11,
                          cursor: 'pointer',
                          fontWeight: 600,
                          transition: 'all 0.15s ease',
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = '#0284c7'; e.currentTarget.style.color = '#fff'; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(15, 23, 42, 0.85)'; e.currentTarget.style.color = '#cbd5e1'; }}
                      >
                        {v.label}
                      </button>
                    ))}
                  </div>

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

        {/* Right Panel: Smart Annotation Studio Controller OR Feature Inspector OR 3D Viewer */}
        <div style={{ width: viewTab === 'smart_annotation' ? '42%' : showFeatureLayer ? '38%' : '33%', minWidth: 340, display: 'flex', flexDirection: 'column', background: '#111', transition: 'width 0.2s ease' }}>
          {viewTab === 'smart_annotation' ? (() => {
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
                (f.type && f.type.toLowerCase().includes(q))
              );
            });

            const allFilteredSelected = filtered.length > 0 && filtered.every(f => selectedFeatureIds.has(f.id));

            return (
              <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#0b1120' }}>
                {/* Panel Header */}
                <div style={{ height: 44, borderBottom: '1px solid #1e293b', background: '#0f172a', display: 'flex', alignItems: 'center', padding: '0 14px', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 700, color: '#c084fc' }}>
                    <Wand2 size={16} />
                    <span>標註控制台 (Annotation Controller)</span>
                  </div>
                  <span style={{ fontSize: 11, background: '#7e22ce', color: '#fff', padding: '2px 8px', borderRadius: 10, fontWeight: 600 }}>
                    {selectedFeatureIds.size} / {featureRecords.length} 已選
                  </span>
                </div>

                {/* 🌟 樣板風格庫與一鍵套用區塊 (Template Preset Section) */}
                <div style={{ padding: '10px 12px', background: '#0f172a', borderBottom: '1px solid #1e293b' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                    <span style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                      風格偏好樣板 (Template Presets)
                    </span>
                    <button
                      onClick={() => setSaveTemplateModalOpen(true)}
                      style={{
                        background: 'transparent',
                        border: '1px solid #38bdf8',
                        borderRadius: 4,
                        color: '#38bdf8',
                        fontSize: 10,
                        padding: '2px 6px',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 3,
                        fontWeight: 600,
                      }}
                    >
                      <Save size={11} />
                      <span>儲存偏好為新樣板</span>
                    </button>
                  </div>

                  <div style={{ display: 'flex', gap: 6 }}>
                    <select
                      value={selectedTemplateId}
                      onChange={(e) => setSelectedTemplateId(e.target.value)}
                      style={{
                        flex: 1,
                        background: '#0b1120',
                        border: '1px solid #334155',
                        borderRadius: 6,
                        color: '#f8fafc',
                        padding: '6px 8px',
                        fontSize: 11,
                        outline: 'none',
                        cursor: 'pointer',
                      }}
                    >
                      {annotationTemplates.map(t => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>

                    <button
                      onClick={() => handleApplyTemplate(selectedTemplateId)}
                      style={{
                        background: 'linear-gradient(135deg, #7c3aed, #2563eb)',
                        border: 'none',
                        borderRadius: 6,
                        color: '#fff',
                        fontSize: 11,
                        padding: '6px 12px',
                        fontWeight: 700,
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                        boxShadow: '0 2px 8px rgba(124, 58, 237, 0.4)',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      <Sparkles size={12} />
                      <span>一鍵套用</span>
                    </button>

                    {!selectedTemplateId.endsWith('_standard_preset') && (
                      <button
                        onClick={() => handleDeleteTemplate(selectedTemplateId)}
                        title="刪除此自訂樣板"
                        style={{
                          background: '#7f1d1d',
                          border: '1px solid #991b1b',
                          borderRadius: 6,
                          color: '#fca5a5',
                          padding: '6px',
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                </div>

                {/* Batch Action Bar */}
                <div style={{ padding: '6px 12px', background: '#0b1120', borderBottom: '1px solid #1e293b', display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      onClick={() => selectAllFeatures(filtered.map(f => f.id))}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                        fontSize: 10,
                        padding: '3px 7px',
                        borderRadius: 4,
                        border: '1px solid #0284c7',
                        background: allFilteredSelected ? '#0284c7' : '#1e293b',
                        color: '#fff',
                        cursor: 'pointer',
                        fontWeight: 600,
                      }}
                    >
                      <CheckSquare size={12} />
                      <span>全選 ({filtered.length})</span>
                    </button>
                    <button
                      onClick={() => deselectAllFeatures(filtered.map(f => f.id))}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                        fontSize: 10,
                        padding: '3px 7px',
                        borderRadius: 4,
                        border: '1px solid #475569',
                        background: '#1e293b',
                        color: '#cbd5e1',
                        cursor: 'pointer',
                        fontWeight: 500,
                      }}
                    >
                      <Square size={12} />
                      <span>取消全選</span>
                    </button>
                  </div>
                  <span style={{ fontSize: 10, color: '#64748b' }}>
                    顯示 {filtered.length} 處特徵
                  </span>
                </div>

                {/* Search & Category Tabs */}
                <div style={{ padding: '6px 12px', background: '#0b1120' }}>
                  <input
                    type="text"
                    value={featureSearch}
                    onChange={(e) => setFeatureSearch(e.target.value)}
                    placeholder="搜尋特徵名稱、ID、直徑、卡簧槽..."
                    style={{ width: '100%', boxSizing: 'border-box', padding: '5px 8px', background: '#0f172a', border: '1px solid #334155', borderRadius: 4, color: '#f8fafc', fontSize: 11 }}
                  />
                </div>

                <div style={{ padding: '0 12px 6px 12px', display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                  {[
                    { id: 'ALL', label: '全部' },
                    { id: 'shaft', label: '軸/配合段' },
                    { id: 'groove', label: '卡簧/凹槽' },
                    { id: 'cone', label: '倒角/錐面' },
                    { id: 'step', label: '階梯' },
                    { id: 'fillet', label: '圓角' },
                    { id: 'hole', label: '孔洞' },
                    { id: 'thickness', label: '壁厚/基準' },
                    { id: 'pattern', label: '孔群' },
                  ].map(tab => (
                    <button
                      key={tab.id}
                      onClick={() => setFeatureFilter(tab.id)}
                      style={{
                        fontSize: 10,
                        padding: '2px 6px',
                        borderRadius: 3,
                        border: 'none',
                        cursor: 'pointer',
                        background: featureFilter === tab.id ? '#7c3aed' : '#1e293b',
                        color: featureFilter === tab.id ? '#fff' : '#94a3b8',
                        fontWeight: featureFilter === tab.id ? 700 : 500
                      }}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {/* Feature Customization List */}
                <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 10px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {filtered.map((feature, idx) => {
                    const isSelected = selectedFeatureIds.has(feature.id);
                    const isHovered = hoveredFeatureId === feature.id;
                    const fType = (feature.type || '').toLowerCase();
                    const cfg = annotationConfig[feature.id] || {};
                    const currentView = cfg.preferred_view || feature.preferred_view || 'front';
                    const currentTol = cfg.tolerance !== undefined ? cfg.tolerance : (feature.tolerance || '');
                    const currentSide = cfg.side || feature.side || 'BOTTOM';

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

                    return (
                      <div
                        key={feature.id || idx}
                        onMouseEnter={() => setHoveredFeatureId(feature.id)}
                        onMouseLeave={() => setHoveredFeatureId(null)}
                        style={{
                          padding: '8px 10px',
                          background: isHovered ? '#1e293b' : isSelected ? '#111c33' : '#0f172a',
                          borderRadius: 6,
                          border: `1px solid ${isHovered ? '#38bdf8' : isSelected ? '#2563eb' : '#1e293b'}`,
                          transition: 'all 0.15s ease',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 6,
                        }}
                      >
                        {/* Header Row: Checkbox + ID + Badge */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', flex: 1 }}>
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleFeature(feature.id)}
                              style={{ cursor: 'pointer', accentColor: '#3b82f6', width: 15, height: 15 }}
                            />
                            <span style={{ fontWeight: 700, color: isSelected ? '#f8fafc' : '#64748b', fontSize: 12 }}>
                              {feature.id || `F${idx + 1}`}
                            </span>
                          </label>
                          <span style={{ fontSize: 9, padding: '2px 6px', borderRadius: 3, background: badgeBg, color: badgeColor, fontWeight: 600 }}>
                            {feature.type}
                          </span>
                        </div>

                        {/* Feature Title */}
                        <div style={{ fontWeight: 600, color: isSelected ? '#38bdf8' : '#94a3b8', fontSize: 12, paddingLeft: 23 }}>
                          {feature.name}
                        </div>

                        {/* Annotation Customization Controls (View, Tolerance, Side) */}
                        {isSelected && (
                          <div style={{ marginLeft: 23, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, background: '#090d16', padding: '6px 8px', borderRadius: 4, border: '1px solid #1e293b' }}>
                            <div>
                              <div style={{ fontSize: 9, color: '#64748b', marginBottom: 2 }}>標註視圖</div>
                              <select
                                value={currentView}
                                onChange={(e) => updateFeatureConfig(feature.id, { preferred_view: e.target.value })}
                                style={{ width: '100%', background: '#0f172a', border: '1px solid #334155', borderRadius: 3, color: '#f8fafc', fontSize: 10, padding: 2 }}
                              >
                                <option value="front">正視圖</option>
                                <option value="top">俯視圖</option>
                                <option value="right">右側視圖</option>
                                <option value="left">左側視圖</option>
                              </select>
                            </div>

                            <div>
                              <div style={{ fontSize: 9, color: '#64748b', marginBottom: 2 }}>公差設定</div>
                              <input
                                type="text"
                                value={currentTol}
                                onChange={(e) => updateFeatureConfig(feature.id, { tolerance: e.target.value })}
                                placeholder="如 ±0.05"
                                style={{ width: '100%', boxSizing: 'border-box', background: '#0f172a', border: '1px solid #334155', borderRadius: 3, color: '#facc15', fontSize: 10, padding: '2px 4px' }}
                              />
                            </div>

                            <div>
                              <div style={{ fontSize: 9, color: '#64748b', marginBottom: 2 }}>標註側向</div>
                              <select
                                value={currentSide}
                                onChange={(e) => updateFeatureConfig(feature.id, { side: e.target.value })}
                                style={{ width: '100%', background: '#0f172a', border: '1px solid #334155', borderRadius: 3, color: '#f8fafc', fontSize: 10, padding: 2 }}
                              >
                                <option value="BOTTOM">底部 (Bottom)</option>
                                <option value="TOP">頂部 (Top)</option>
                                <option value="LEFT">左側 (Left)</option>
                                <option value="RIGHT">右側 (Right)</option>
                              </select>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Sticky Action Footer */}
                <div style={{ padding: '12px', background: '#0f172a', borderTop: '1px solid #1e293b' }}>
                  <button
                    onClick={handleRenderCustomDrawing}
                    disabled={isRenderingDrawing || selectedFeatureIds.size === 0}
                    style={{
                      width: '100%',
                      background: selectedFeatureIds.size > 0 ? 'linear-gradient(135deg, #0284c7, #7c3aed)' : '#334155',
                      color: '#fff',
                      border: 'none',
                      borderRadius: 6,
                      padding: '10px 0',
                      fontSize: 13,
                      fontWeight: 700,
                      cursor: selectedFeatureIds.size > 0 ? 'pointer' : 'not-allowed',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: 8,
                      boxShadow: selectedFeatureIds.size > 0 ? '0 4px 14px rgba(2, 132, 199, 0.4)' : 'none',
                    }}
                  >
                    {isRenderingDrawing ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
                    <span>{isRenderingDrawing ? '正在產出客製工程圖...' : `🚀 產出新版標註圖紙 (${selectedFeatureIds.size} 特徵)`}</span>
                  </button>
                </div>
              </div>
            );
          })() : showFeatureLayer ? (() => {
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
                (f.type && f.type.toLowerCase().includes(q))
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

                {/* Batch Action Bar */}
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
                  <input
                    type="text"
                    value={featureSearch}
                    onChange={(e) => setFeatureSearch(e.target.value)}
                    placeholder="搜尋特徵名稱、ID、直徑、尺寸..."
                    style={{ width: '100%', boxSizing: 'border-box', padding: '6px 10px', background: '#0f172a', border: '1px solid #334155', borderRadius: 6, color: '#f8fafc', fontSize: 11 }}
                  />
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
                    gl={{ antialias: true, powerPreference: 'high-performance' }}
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

      {/* 🌟 儲存自訂樣板 Modal (Save Template Preset Modal) */}
      {saveTemplateModalOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
          <div style={{ width: 440, background: '#0f172a', border: '1px solid #334155', borderRadius: 12, padding: 24, boxShadow: '0 20px 50px rgba(0,0,0,0.8)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <Save size={20} color="#38bdf8" />
              <h3 style={{ margin: 0, fontSize: 16, color: '#f8fafc', fontWeight: 700 }}>儲存標註偏好為新樣板</h3>
            </div>

            <p style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16, lineHeight: 1.5 }}>
              系統將會記住目前已勾選的 <strong>{selectedFeatureIds.size}</strong> 處特徵類型、自訂公差與視圖偏好。下次載入任何相似的新模型時，點選「一鍵套用」即可自動批次標註！
            </p>

            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 12, color: '#cbd5e1', marginBottom: 6, fontWeight: 600 }}>
                樣板名稱 (Template Name)
              </label>
              <input
                type="text"
                value={newTemplateName}
                onChange={(e) => setNewTemplateName(e.target.value)}
                placeholder="例如：精密風扇軸標準風格、葉輪轉子精密級..."
                style={{ width: '100%', boxSizing: 'border-box', padding: '8px 12px', background: '#0b1120', border: '1px solid #3b82f6', borderRadius: 6, color: '#f8fafc', fontSize: 13 }}
                autoFocus
              />
            </div>

            <div style={{ marginBottom: 20 }}>
              <label style={{ display: 'block', fontSize: 12, color: '#cbd5e1', marginBottom: 6, fontWeight: 600 }}>
                樣板說明 (Description)
              </label>
              <textarea
                value={newTemplateDesc}
                onChange={(e) => setNewTemplateDesc(e.target.value)}
                placeholder="簡短描述此樣板的適用範圍或特殊公差需求..."
                rows={3}
                style={{ width: '100%', boxSizing: 'border-box', padding: '8px 12px', background: '#0b1120', border: '1px solid #334155', borderRadius: 6, color: '#f8fafc', fontSize: 12, resize: 'none' }}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button
                onClick={() => setSaveTemplateModalOpen(false)}
                style={{ padding: '8px 16px', background: '#1e293b', border: '1px solid #475569', borderRadius: 6, color: '#cbd5e1', fontSize: 13, cursor: 'pointer', fontWeight: 600 }}
              >
                取消
              </button>
              <button
                onClick={handleSaveNewTemplate}
                disabled={!newTemplateName.trim()}
                style={{ padding: '8px 18px', background: newTemplateName.trim() ? 'linear-gradient(135deg, #0284c7, #2563eb)' : '#334155', border: 'none', borderRadius: 6, color: '#fff', fontSize: 13, cursor: newTemplateName.trim() ? 'pointer' : 'not-allowed', fontWeight: 700 }}
              >
                確認儲存樣板
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
