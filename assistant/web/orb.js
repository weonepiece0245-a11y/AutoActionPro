// 3D 全息球:核心球体(随声音起伏)+ 线框外壳 + 三道陀螺光环 + 粒子云,带辉光。
// state.mode: idle | thinking | speaking | listening;state.level: 0~1 的音量。
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const COLORS = {
  idle: new THREE.Color("#22d3ee"),
  thinking: new THREE.Color("#3b82f6"),
  speaking: new THREE.Color("#7df9ff"),
  listening: new THREE.Color("#d946ef"),
};

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
    vec3 col = uColor * (0.18 + fres * 1.7) + vec3(1.0) * (fres * 0.35 + bands * 0.6) * (0.6 + uLevel);
    gl_FragColor = vec4(col, 0.22 + fres * 0.8);
  }
`;

export function start(canvas, state) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(0, 0.35, 7.2);

  const group = new THREE.Group();
  scene.add(group);

  // 核心
  const coreUniforms = { uTime: { value: 0 }, uLevel: { value: 0 }, uColor: { value: COLORS.idle.clone() } };
  const core = new THREE.Mesh(
    new THREE.SphereGeometry(1.15, 160, 160),
    new THREE.ShaderMaterial({
      uniforms: coreUniforms, vertexShader: coreVertex, fragmentShader: coreFragment,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }),
  );
  group.add(core);

  // 内核亮点
  const heart = new THREE.Mesh(
    new THREE.SphereGeometry(0.32, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.55 }),
  );
  group.add(heart);

  // 线框外壳
  const shellMaterial = new THREE.LineBasicMaterial({ color: COLORS.idle.clone(), transparent: true, opacity: 0.3 });
  const shell = new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(1.75, 1)), shellMaterial);
  group.add(shell);

  // 陀螺光环
  const rings = [];
  const ringSpecs = [
    { r: 2.2, tilt: [Math.PI / 2.2, 0, 0], speed: 0.35 },
    { r: 2.45, tilt: [Math.PI / 3, Math.PI / 4, 0], speed: -0.25 },
    { r: 2.7, tilt: [0.2, Math.PI / 2.4, 0.4], speed: 0.18 },
  ];
  for (const spec of ringSpecs) {
    const ringMaterial = new THREE.MeshBasicMaterial({ color: COLORS.idle.clone(), transparent: true, opacity: 0.75 });
    const ring = new THREE.Mesh(new THREE.TorusGeometry(spec.r, 0.008, 8, 220), ringMaterial);
    ring.rotation.set(...spec.tilt);
    // 光环上的小光点
    const bead = new THREE.Mesh(new THREE.SphereGeometry(0.045, 12, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    bead.position.x = spec.r;
    ring.add(bead);
    ring.userData.speed = spec.speed;
    group.add(ring);
    rings.push(ring);
  }

  // 粒子云
  const count = 2200;
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const radius = 2.4 + Math.random() * 1.6;
    const theta = Math.random() * Math.PI * 2;
    const y = (Math.random() - 0.5) * 0.9 * Math.exp(-Math.random() * 2);
    positions[i * 3] = Math.cos(theta) * radius;
    positions[i * 3 + 1] = y;
    positions[i * 3 + 2] = Math.sin(theta) * radius;
  }
  const particleGeometry = new THREE.BufferGeometry();
  particleGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const particleMaterial = new THREE.PointsMaterial({
    color: COLORS.idle.clone(), size: 0.028, transparent: true, opacity: 0.85,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const particles = new THREE.Points(particleGeometry, particleMaterial);
  particles.rotation.x = 0.28;
  scene.add(particles);

  // 辉光
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
    // 球放在画面中上方,给下面的对话留空间
    camera.setViewOffset(w, h, 0, h * 0.14, w, h);
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  resize();

  const clock = new THREE.Clock();
  const color = COLORS.idle.clone();
  let level = 0;
  let spin = 0;

  function frame() {
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;
    const mode = state.mode || "idle";
    const target = COLORS[mode] || COLORS.idle;
    color.lerp(target, 0.06);

    const wanted = mode === "speaking" || mode === "listening" ? state.level || 0 : mode === "thinking" ? 0.18 + 0.12 * Math.sin(t * 6) : 0;
    level += (wanted - level) * 0.25;

    const speed = mode === "thinking" ? 2.6 : mode === "speaking" ? 1.4 : 0.6;
    spin += dt * speed;

    coreUniforms.uTime.value = t;
    coreUniforms.uLevel.value = level;
    coreUniforms.uColor.value.copy(color);
    core.rotation.y = spin * 0.35;
    core.rotation.x = Math.sin(t * 0.3) * 0.2;
    heart.scale.setScalar(1 + level * 1.6 + Math.sin(t * 2) * 0.04);
    heart.material.opacity = 0.35 + level * 0.6;

    shell.rotation.y = -spin * 0.22;
    shell.rotation.z = spin * 0.08;
    shellMaterial.color.copy(color);
    shellMaterial.opacity = 0.22 + level * 0.4;

    for (const ring of rings) {
      ring.rotation.z += dt * ring.userData.speed * speed;
      ring.material.color.copy(color);
    }
    particles.rotation.y += dt * 0.05 * speed;
    particleMaterial.color.copy(color);
    particleMaterial.size = 0.026 + level * 0.03;

    group.position.y = Math.sin(t * 0.8) * 0.06;
    bloom.strength = 1.0 + level * 1.2;

    composer.render();
    requestAnimationFrame(frame);
  }
  frame();
}
