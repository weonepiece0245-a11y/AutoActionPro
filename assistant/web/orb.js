// 3D 全息光球,有几种样子(state.style):
//   classic 经典:核心球体 + 线框外壳 + 三道陀螺光环 + 粒子云
//   reactor 反应堆:正面的能量核心、三角、一圈线圈和刻度环(钢铁侠胸口那种)
//   galaxy  星系:旋臂粒子 + 发光核心
//   wave    声波环:一圈会跟着声音跳的光条
//   atom    原子:原子核 + 三条电子轨道
// state.mode: idle | thinking | speaking | listening;state.level: 0~1 的音量。
// 互动:滚轮放大缩小、拖着上下左右转(放手有惯性)、点光球开始说话;
// state.zoomBy(倍数)、state.resetView()、state.style 给外面的按钮用。
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const COLORS = {
  idle: new THREE.Color("#22d3ee"),
  thinking: new THREE.Color("#3b82f6"),
  speaking: new THREE.Color("#2bc4e6"),
  listening: new THREE.Color("#d946ef"),
};
export const STYLES = ["classic", "reactor", "galaxy", "wave", "atom"];
const ZOOM_MIN = 0.55, ZOOM_MAX = 1.9, CAMERA_Z = 7.2;

const coreVertex = /* glsl */ `
  uniform float uTime;
  uniform float uLevel;
  varying vec3 vNormal;
  varying vec3 vView;
  varying float vWave;
  void main() {
    vec3 p = position;
    float w = sin(p.x * 5.0 + uTime * 2.1) * sin(p.y * 6.0 + uTime * 1.7) * sin(p.z * 4.5 + uTime * 1.3);
    float w2 = sin(p.y * 13.0 - uTime * 4.0) * 0.35;
    float amount = 0.035 + uLevel * 0.42;
    p += normal * (w + w2 * uLevel) * amount;
    vWave = w;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    vNormal = normalize(normalMatrix * normal);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`;

const coreFragment = /* glsl */ `
  uniform vec3 uColor;
  uniform float uLevel;
  uniform float uTime;
  varying vec3 vNormal;
  varying vec3 vView;
  varying float vWave;
  void main() {
    float fres = pow(1.0 - max(dot(vNormal, vView), 0.0), 2.2);
    float bands = smoothstep(0.92, 1.0, sin(vWave * 18.0 + uTime * 3.0)) * (0.25 + uLevel);
    vec3 col = uColor * (0.16 + fres * 1.3) + vec3(1.0) * (fres * 0.18 + bands * 0.2) * (0.6 + uLevel * 0.15);
    gl_FragColor = vec4(col, 0.2 + fres * 0.7);
  }
`;

const glow = (opacity = 1) => new THREE.MeshBasicMaterial({
  color: COLORS.idle.clone(), transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false,
});

// ======================== 各种样子 ========================

function buildClassic() {
  const group = new THREE.Group();
  const uniforms = { uTime: { value: 0 }, uLevel: { value: 0 }, uColor: { value: COLORS.idle.clone() } };
  const core = new THREE.Mesh(new THREE.SphereGeometry(1.15, 160, 160), new THREE.ShaderMaterial({
    uniforms, vertexShader: coreVertex, fragmentShader: coreFragment,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  }));
  const heart = new THREE.Mesh(new THREE.SphereGeometry(0.32, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.55 }));
  const shellMaterial = new THREE.LineBasicMaterial({ color: COLORS.idle.clone(), transparent: true, opacity: 0.3 });
  const shell = new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(1.75, 1)), shellMaterial);
  group.add(core, heart, shell);
  const rings = [];
  for (const spec of [
    { r: 2.2, tilt: [Math.PI / 2.2, 0, 0], speed: 0.35 },
    { r: 2.45, tilt: [Math.PI / 3, Math.PI / 4, 0], speed: -0.25 },
    { r: 2.7, tilt: [0.2, Math.PI / 2.4, 0.4], speed: 0.18 },
  ]) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(spec.r, 0.008, 8, 220),
      new THREE.MeshBasicMaterial({ color: COLORS.idle.clone(), transparent: true, opacity: 0.75 }));
    ring.rotation.set(...spec.tilt);
    const bead = new THREE.Mesh(new THREE.SphereGeometry(0.045, 12, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    bead.position.x = spec.r;
    ring.add(bead);
    ring.userData.speed = spec.speed;
    group.add(ring);
    rings.push(ring);
  }
  const count = 2200;
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const radius = 2.4 + Math.random() * 1.6, theta = Math.random() * Math.PI * 2;
    positions[i * 3] = Math.cos(theta) * radius;
    positions[i * 3 + 1] = (Math.random() - 0.5) * 0.9 * Math.exp(-Math.random() * 2);
    positions[i * 3 + 2] = Math.sin(theta) * radius;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const dustMaterial = new THREE.PointsMaterial({ color: COLORS.idle.clone(), size: 0.028, transparent: true, opacity: 0.85,
    blending: THREE.AdditiveBlending, depthWrite: false });
  const dust = new THREE.Points(geometry, dustMaterial);
  dust.rotation.x = 0.28;
  group.add(dust);
  return {
    group,
    update({ t, dt, level, color, spin, speed }) {
      uniforms.uTime.value = t; uniforms.uLevel.value = level; uniforms.uColor.value.copy(color);
      core.rotation.y = spin * 0.35; core.rotation.x = Math.sin(t * 0.3) * 0.2;
      heart.scale.setScalar(1 + level * 0.4 + Math.sin(t * 2) * 0.04);
      heart.material.opacity = 0.25 + level * 0.1;
      shell.rotation.y = -spin * 0.22; shell.rotation.z = spin * 0.08;
      shellMaterial.color.copy(color); shellMaterial.opacity = 0.2 + level * 0.08;
      for (const ring of rings) { ring.rotation.z += dt * ring.userData.speed * speed; ring.material.color.copy(color); }
      dust.rotation.y += dt * 0.05 * speed;
      dustMaterial.color.copy(color); dustMaterial.size = 0.026 + level * 0.006;
    },
  };
}

function buildReactor() {
  const group = new THREE.Group();
  const core = new THREE.Mesh(new THREE.CircleGeometry(0.62, 64), glow(0.9));
  const coreWhite = new THREE.Mesh(new THREE.CircleGeometry(0.34, 48),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9 }));
  coreWhite.position.z = 0.01;
  const triShape = new THREE.BufferGeometry().setFromPoints(
    [0, 1, 2, 0].map((i) => new THREE.Vector3(Math.cos(Math.PI / 2 + i * 2 * Math.PI / 3) * 0.95, Math.sin(Math.PI / 2 + i * 2 * Math.PI / 3) * 0.95, 0.02)));
  const triMaterial = new THREE.LineBasicMaterial({ color: COLORS.idle.clone(), transparent: true, opacity: 0.95 });
  const triangle = new THREE.Line(triShape, triMaterial);
  group.add(core, coreWhite, triangle);
  const rings = [1.08, 1.95, 2.55].map((r, i) => {
    const ring = new THREE.Mesh(new THREE.RingGeometry(r, r + (i === 1 ? 0.05 : 0.025), 128), glow(0.75));
    group.add(ring);
    return ring;
  });
  const coils = [];
  for (let i = 0; i < 10; i++) {  // 一圈线圈
    const a = i / 10 * Math.PI * 2;
    const coil = new THREE.Mesh(new THREE.PlaneGeometry(0.36, 0.62), glow(0.7));
    coil.position.set(Math.cos(a) * 1.5, Math.sin(a) * 1.5, 0);
    coil.rotation.z = a + Math.PI / 2;
    group.add(coil);
    coils.push(coil);
  }
  const ticks = new THREE.Group();  // 外圈刻度,慢慢反转
  for (let i = 0; i < 48; i++) {
    const a = i / 48 * Math.PI * 2;
    const tick = new THREE.Mesh(new THREE.PlaneGeometry(i % 4 === 0 ? 0.05 : 0.025, i % 4 === 0 ? 0.28 : 0.16), glow(0.8));
    tick.position.set(Math.cos(a) * 2.85, Math.sin(a) * 2.85, 0);
    tick.rotation.z = a + Math.PI / 2;
    ticks.add(tick);
  }
  group.add(ticks);
  const all = [core, ...rings, ...coils, ...ticks.children];
  return {
    group,
    update({ t, dt, level, color, speed }) {
      for (const m of all) m.material.color.copy(color);
      triMaterial.color.copy(color);
      core.scale.setScalar(1 + level * 0.25 + Math.sin(t * 3) * 0.03);
      coreWhite.material.opacity = 0.65 + level * 0.3;
      triangle.rotation.z += dt * 0.3 * speed;
      coils.forEach((coil, i) => {
        coil.material.opacity = 0.35 + 0.35 * (0.5 + 0.5 * Math.sin(t * 4 * speed - i * 0.63)) + level * 0.3;
        coil.scale.y = 1 + level * 0.35 * (0.5 + 0.5 * Math.sin(t * 9 + i));
      });
      ticks.rotation.z -= dt * 0.12 * speed;
      rings[1].rotation.z += dt * 0.2;
      rings[2].material.opacity = 0.45 + level * 0.4;
    },
  };
}

function buildGalaxy() {
  const group = new THREE.Group();
  const count = 7000;
  const positions = new Float32Array(count * 3);
  const shade = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const arm = i % 3;
    const r = Math.pow(Math.random(), 1.6) * 3.2 + 0.15;
    const angle = arm / 3 * Math.PI * 2 + r * 1.7 + (Math.random() - 0.5) * 0.5 / (r * 0.6 + 0.4);
    const spread = (Math.random() - 0.5) * 0.25 * (1 - r / 3.6);
    positions[i * 3] = Math.cos(angle) * r + (Math.random() - 0.5) * 0.12;
    positions[i * 3 + 1] = spread;
    positions[i * 3 + 2] = Math.sin(angle) * r + (Math.random() - 0.5) * 0.12;
    shade[i] = r;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const colors = new Float32Array(count * 3);
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const material = new THREE.PointsMaterial({ size: 0.032, vertexColors: true, transparent: true, opacity: 0.95,
    blending: THREE.AdditiveBlending, depthWrite: false });
  const stars = new THREE.Points(geometry, material);
  const core = new THREE.Mesh(new THREE.SphereGeometry(0.28, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9 }));
  const halo = new THREE.Mesh(new THREE.SphereGeometry(0.62, 32, 32), glow(0.35));
  group.add(stars, core, halo);
  group.rotation.x = 1.05;  // 斜着看星系盘
  const white = new THREE.Color(0xffffff);
  const tmp = new THREE.Color();
  let painted = null;
  return {
    group,
    update({ t, dt, level, color, speed }) {
      if (!painted || !painted.equals(color)) {  // 颜色变了才重新上色:中间偏白,外面是主题色
        painted = color.clone();
        for (let i = 0; i < count; i++) {
          tmp.copy(white).lerp(color, Math.min(1, shade[i] / 1.4));
          colors[i * 3] = tmp.r; colors[i * 3 + 1] = tmp.g; colors[i * 3 + 2] = tmp.b;
        }
        geometry.attributes.color.needsUpdate = true;
      }
      stars.rotation.y += dt * (0.12 + level * 0.5) * speed;
      const pulse = 1 + level * 0.18 + Math.sin(t * 1.5) * 0.02;
      stars.scale.set(pulse, 1 + level * 0.8, pulse);
      core.scale.setScalar(1 + level * 0.6);
      halo.material.color.copy(color);
      halo.scale.setScalar(1 + level * 0.5 + Math.sin(t * 2) * 0.05);
      material.size = 0.03 + level * 0.012;
    },
  };
}

function buildWave() {
  const group = new THREE.Group();
  const bars = [];
  const count = 96;
  for (let i = 0; i < count; i++) {
    const a = i / count * Math.PI * 2;
    const pivot = new THREE.Group();
    pivot.rotation.z = a - Math.PI / 2;
    const bar = new THREE.Mesh(new THREE.PlaneGeometry(0.045, 1), glow(0.85));
    bar.geometry.translate(0, 0.5, 0);  // 从底部往外长
    bar.position.y = 1.55;
    pivot.add(bar);
    group.add(pivot);
    bars.push(bar);
  }
  const inner = new THREE.Mesh(new THREE.RingGeometry(1.42, 1.47, 160), glow(0.9));
  const sphere = new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(1.1, 2)),
    new THREE.LineBasicMaterial({ color: COLORS.idle.clone(), transparent: true, opacity: 0.22 }));
  const dot = new THREE.Mesh(new THREE.SphereGeometry(0.22, 24, 24), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.8 }));
  group.add(inner, sphere, dot);
  const heights = new Float32Array(count);
  return {
    group,
    update({ t, dt, level, color, speed, spin }) {
      bars.forEach((bar, i) => {
        const a = i / count * Math.PI * 2;
        const idle = 0.12 + 0.08 * Math.sin(t * 1.4 + a * 3) * Math.sin(t * 0.7 + a * 5);
        const voice = level * (0.5 + 0.5 * Math.abs(Math.sin(a * 7 + t * 9) * Math.cos(a * 3 - t * 5))) * 1.6;
        const want = Math.max(0.05, idle + voice + (speed > 2 ? 0.25 * (0.5 + 0.5 * Math.sin(a * 2 - t * 8)) : 0));
        heights[i] += (want - heights[i]) * Math.min(1, dt * 14);
        bar.scale.y = heights[i];
        bar.material.color.copy(color);
      });
      inner.material.color.copy(color);
      sphere.material.color.copy(color);
      sphere.rotation.y = spin * 0.3; sphere.rotation.x = spin * 0.17;
      group.rotation.z += dt * 0.05 * speed;
      dot.scale.setScalar(1 + level * 0.7);
    },
  };
}

function buildAtom() {
  const group = new THREE.Group();
  const nucleus = new THREE.Group();
  for (let i = 0; i < 14; i++) {
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.17, 20, 20), glow(0.75));
    ball.position.set((Math.random() - 0.5) * 0.42, (Math.random() - 0.5) * 0.42, (Math.random() - 0.5) * 0.42);
    nucleus.add(ball);
  }
  const heart = new THREE.Mesh(new THREE.SphereGeometry(0.2, 20, 20), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  nucleus.add(heart);
  group.add(nucleus);
  const orbits = [];
  for (const [i, tilt] of [[0, [1.2, 0, 0]], [1, [1.2, 0, Math.PI / 3]], [2, [1.2, 0, -Math.PI / 3]]]) {
    const plane = new THREE.Group();
    plane.rotation.set(...tilt);
    const path = new THREE.Mesh(new THREE.TorusGeometry(1, 0.01, 8, 200), glow(0.6));
    path.scale.set(2.5, 1.15, 1);
    const electron = new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 16), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    const trail = new THREE.Mesh(new THREE.SphereGeometry(0.2, 16, 16), glow(0.4));
    plane.add(path, electron, trail);
    group.add(plane);
    orbits.push({ path, electron, trail, phase: i * 2.1 });
  }
  return {
    group,
    update({ t, dt, level, color, speed, spin }) {
      nucleus.rotation.y = spin * 0.5; nucleus.rotation.x = spin * 0.3;
      nucleus.scale.setScalar(1 + level * 0.35 + Math.sin(t * 2.5) * 0.03);
      for (const ball of nucleus.children) if (ball !== heart) ball.material.color.copy(color);
      for (const o of orbits) {
        o.phase += dt * (1.1 + level * 3) * speed;
        const x = Math.cos(o.phase) * 2.5, y = Math.sin(o.phase) * 1.15;
        o.electron.position.set(x, y, 0);
        o.trail.position.set(x, y, 0);
        o.trail.scale.setScalar(1 + level);
        o.path.material.color.copy(color);
        o.trail.material.color.copy(color);
      }
    },
  };
}

const BUILDERS = { classic: buildClassic, reactor: buildReactor, galaxy: buildGalaxy, wave: buildWave, atom: buildAtom };

// ======================== 画面 ========================

export function start(canvas, state) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(0, 0.35, CAMERA_Z);

  const group = new THREE.Group();  // 整颗光球:跟着鼠标、被拖着转
  scene.add(group);
  const looks = {};  // 用过的样子留着,切回来不用重做
  let look = null;
  let lookName = "";
  function useStyle(name) {
    if (!BUILDERS[name]) name = "classic";
    if (name === lookName) return;
    if (look) group.remove(look.group);
    looks[name] = looks[name] || BUILDERS[name]();
    look = looks[name];
    lookName = name;
    group.add(look.group);
  }
  useStyle(state.style || "classic");

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 1.15, 0.65, 0.0);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  function resize() {
    const w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    camera.aspect = w / h;
    camera.setViewOffset(w, h, 0, h * 0.14, w, h);  // 球放在画面中上方,给下面的对话留空间
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  resize();

  const clock = new THREE.Clock();
  const color = COLORS.idle.clone();
  let level = 0;
  let spin = 0;

  // ---------- 放大缩小、转 ----------
  const view = { zoom: Number(state.zoom) || 1, wantZoom: Number(state.zoom) || 1, rx: 0, ry: 0, vx: 0, vy: 0 };
  const clampZoom = (z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
  view.zoom = view.wantZoom = clampZoom(view.zoom);
  state.zoomBy = (factor) => { view.wantZoom = clampZoom(view.wantZoom * factor); state.onView && state.onView(view.wantZoom); };
  state.resetView = () => { view.wantZoom = 1; view.vx = view.vy = 0; view.reset = true; state.onView && state.onView(1); };
  state.setStyle = (name) => { state.style = name; useStyle(name); };

  const pointer = { x: 0, y: 0 };  // -1~1
  const tilt = { x: 0, y: 0 };
  let dragging = null;  // {x, y, moved, at}
  let hover = 0;
  const center = new THREE.Vector3();
  const edge = new THREE.Vector3();
  const pinch = new Map();  // 触控板 / 触屏两指缩放
  let pinchStart = 0;

  function orbOnScreen() {
    group.getWorldPosition(center);
    edge.copy(center).add(new THREE.Vector3(1.75, 0, 0));
    center.project(camera);
    edge.project(camera);
    const w = window.innerWidth, h = window.innerHeight;
    return { cx: (center.x + 1) / 2 * w, cy: (1 - center.y) / 2 * h, r: Math.abs((edge.x - center.x) / 2 * w) };
  }
  function overOrb(x, y) {
    const { cx, cy, r } = orbOnScreen();
    return Math.hypot(x - cx, y - cy) <= r;
  }
  window.addEventListener("pointermove", (e) => {
    pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
    pointer.y = (e.clientY / window.innerHeight) * 2 - 1;
    if (pinch.has(e.pointerId)) pinch.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch.size === 2) {
      const [a, b] = [...pinch.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinchStart) state.zoomBy(d / pinchStart);
      pinchStart = d;
      return;
    }
    if (dragging) {
      const dx = e.clientX - dragging.x, dy = e.clientY - dragging.y;
      dragging.moved = Math.max(dragging.moved, Math.hypot(e.clientX - dragging.x0, e.clientY - dragging.y0));
      view.vy = dx * 0.006;  // 左右拖:绕竖轴转
      view.vx = dy * 0.006;  // 上下拖:绕横轴转
      view.ry += view.vy; view.rx += view.vx;
      dragging.x = e.clientX; dragging.y = e.clientY;
    }
    canvas.style.cursor = dragging ? "grabbing" : overOrb(e.clientX, e.clientY) ? "pointer" : "";
  });
  canvas.addEventListener("pointerdown", (e) => {
    pinch.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch.size === 2) { dragging = null; pinchStart = 0; return; }
    dragging = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, moved: 0, at: performance.now(),
      onOrb: overOrb(e.clientX, e.clientY) };
    canvas.setPointerCapture(e.pointerId);
  });
  const release = (e) => {
    pinch.delete(e.pointerId);
    pinchStart = 0;
    const d = dragging;
    dragging = null;
    if (e.type === "pointerup" && d && d.onOrb && d.moved < 6 && performance.now() - d.at < 400 && state.onOrbClick) state.onOrbClick();
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("wheel", (e) => {  // 滚轮:往上放大,往下缩小(只在光球这一层,对话框照常滚动)
    e.preventDefault();
    state.zoomBy(Math.exp(-e.deltaY * 0.0012));
  }, { passive: false });
  canvas.addEventListener("dblclick", (e) => { if (!overOrb(e.clientX, e.clientY)) state.resetView(); });

  let appliedPalette = null;
  function frame() {
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;
    if (state.palette && state.palette !== appliedPalette) {
      appliedPalette = state.palette;
      for (const k of Object.keys(COLORS)) if (state.palette[k]) COLORS[k].set(state.palette[k]);
    }
    if (state.style && state.style !== lookName) useStyle(state.style);
    const mode = state.mode || "idle";
    color.lerp(COLORS[mode] || COLORS.idle, 0.06);

    const wanted = mode === "speaking" || mode === "listening" ? state.level || 0 : mode === "thinking" ? 0.18 + 0.12 * Math.sin(t * 6) : 0;
    level += (wanted - level) * 0.25;
    const speed = mode === "thinking" ? 2.6 : mode === "speaking" ? 1.4 : 0.6;
    spin += dt * speed;

    // 拖动的转向:放手后惯性慢慢停;横轴转最多到侧面
    if (!dragging) {
      view.ry += view.vy; view.rx += view.vx;
      view.vy *= 0.94; view.vx *= 0.94;
    }
    if (view.reset) {  // 双击空白处 / ⟲:慢慢转回正面
      view.rx *= 0.85; view.ry *= 0.85;
      if (Math.abs(view.rx) + Math.abs(view.ry) < 0.002) { view.rx = view.ry = 0; view.reset = false; }
    }
    view.rx = Math.max(-1.4, Math.min(1.4, view.rx));
    view.zoom += (view.wantZoom - view.zoom) * 0.15;
    camera.position.z = CAMERA_Z / view.zoom;

    tilt.x += (pointer.y * 0.25 - tilt.x) * 0.05;  // 跟着鼠标微微转向你
    tilt.y += (pointer.x * 0.35 - tilt.y) * 0.05;
    group.rotation.x = tilt.x + view.rx;
    group.rotation.y = tilt.y + view.ry;
    hover += ((canvas.style.cursor === "pointer" ? 1 : 0) - hover) * 0.1;

    look.update({ t, dt, level, color, spin, speed });
    group.position.y = Math.sin(t * 0.8) * 0.06;
    bloom.strength = 0.65 + level * 0.12 + hover * 0.12;

    composer.render();
    requestAnimationFrame(frame);
  }
  frame();
}
