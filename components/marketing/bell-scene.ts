// The hero's front-desk service bell, built procedurally so there is no model file to download.
// It draws only when something changes (a press, a drag, the intro), so an idle page uses no GPU time.
import {
  BackSide,
  BoxGeometry,
  CanvasTexture,
  Color,
  CylinderGeometry,
  Group,
  LatheGeometry,
  Mesh,
  MeshBasicMaterial,
  MeshPhysicalMaterial,
  NeutralToneMapping,
  PerspectiveCamera,
  PlaneGeometry,
  PMREMGenerator,
  Raycaster,
  RepeatWrapping,
  Scene,
  ShaderMaterial,
  SRGBColorSpace,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";

export interface BellScene {
  ring(): void;
  dispose(): void;
}

interface Options {
  fontFamily: string;
  onReady(): void;
  onPress(): void;
  onLost(): void;
}

const P = (x: number, y: number) => new Vector2(x, y);

// Rounds every interior corner of a profile so edges catch light like machined metal.
function bevel(points: Vector2[], radius: number, steps = 5): Vector2[] {
  const out: Vector2[] = [points[0]];
  for (let i = 1; i < points.length - 1; i++) {
    const [a, p, b] = [points[i - 1], points[i], points[i + 1]];
    const r = Math.min(radius, p.distanceTo(a) * 0.45, p.distanceTo(b) * 0.45);
    const p1 = p.clone().add(a.clone().sub(p).setLength(r));
    const p2 = p.clone().add(b.clone().sub(p).setLength(r));
    for (let s = 0; s <= steps; s++) {
      const t = s / steps;
      out.push(
        new Vector2(
          (1 - t) * (1 - t) * p1.x + 2 * (1 - t) * t * p.x + t * t * p2.x,
          (1 - t) * (1 - t) * p1.y + 2 * (1 - t) * t * p.y + t * t * p2.y,
        ),
      );
    }
  }
  out.push(points[points.length - 1]);
  return out;
}

function domeProfile(): Vector2[] {
  const points = bevel([P(0.9, 0.33), P(0.982, 0.335), P(1.01, 0.372), P(0.985, 0.405)], 0.016);
  const steps = 72;
  for (let i = 1; i <= steps; i++) {
    const theta = (i / steps) * Math.PI * 0.46;
    points.push(P(0.985 * Math.pow(Math.cos(theta), 0.84), 0.405 + 0.835 * Math.pow(Math.sin(theta), 0.97)));
  }
  return points;
}

function canvas2d(width: number, height: number) {
  const c = document.createElement("canvas");
  c.width = width;
  c.height = height;
  return { c, g: c.getContext("2d") as CanvasRenderingContext2D };
}

function colorTexture(c: HTMLCanvasElement) {
  const t = new CanvasTexture(c);
  t.colorSpace = SRGBColorSpace;
  return t;
}

// Engraved lettering around the base: it is what shows the bell turning as the camera moves.
function bandTexture(fontFamily: string, anisotropy: number) {
  const { c, g } = canvas2d(4096, 128);
  g.fillStyle = "#101d45";
  g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = "#e0d4b4";
  g.font = `500 46px ${fontFamily}`;
  g.textAlign = "center";
  g.textBaseline = "middle";
  if ("letterSpacing" in g) g.letterSpacing = "9px";
  const slot = c.width / 6;
  for (let i = 0; i < 6; i++) {
    g.fillText(i % 2 ? "RING FOR SERVICE" : "ORBITECH", (i + 0.5) * slot, 67);
    g.fillText("·", (i + 1) * slot, 67);
  }
  const t = colorTexture(c);
  t.wrapS = RepeatWrapping;
  t.anisotropy = anisotropy;
  // Centres the first "ORBITECH" on the side of the bell that faces the camera at rest.
  t.offset.x = 1 / 12 - REST_AZIMUTH / (Math.PI * 2);
  return t;
}

// The shadows are painted once rather than computed, which keeps them soft and costs phones nothing.
function contactShadowTexture() {
  const { c, g } = canvas2d(512, 512);
  const fill = g.createRadialGradient(256, 256, 0, 256, 256, 256);
  fill.addColorStop(0, "rgba(38,30,22,0.92)");
  fill.addColorStop(0.555, "rgba(38,30,22,0.9)");
  fill.addColorStop(0.585, "rgba(38,30,22,0.5)");
  fill.addColorStop(0.68, "rgba(38,30,22,0.14)");
  fill.addColorStop(0.86, "rgba(38,30,22,0.03)");
  fill.addColorStop(1, "rgba(38,30,22,0)");
  g.fillStyle = fill;
  g.fillRect(0, 0, 512, 512);
  return colorTexture(c);
}

function castShadowTexture() {
  const { c, g } = canvas2d(512, 512);
  g.translate(256, 256);
  g.scale(1, 0.62);
  const fill = g.createRadialGradient(0, 0, 0, 0, 0, 256);
  fill.addColorStop(0, "rgba(44,34,24,0.34)");
  fill.addColorStop(0.45, "rgba(44,34,24,0.2)");
  fill.addColorStop(0.75, "rgba(44,34,24,0.06)");
  fill.addColorStop(1, "rgba(44,34,24,0)");
  g.fillStyle = fill;
  g.fillRect(-256, -256 / 0.62, 512, 512 / 0.62);
  return colorTexture(c);
}

// A photo-studio room in miniature: the softboxes and black cards are what make polished metal read as metal.
function studioEnvironment() {
  const room = new Scene();
  const add = (w: number, h: number, pos: [number, number, number], value: [number, number, number]) => {
    const m = new Mesh(new PlaneGeometry(w, h), new MeshBasicMaterial({ color: new Color(...value), side: 2 }));
    m.position.set(...pos);
    m.lookAt(0, 0.6, 0);
    room.add(m);
  };
  room.add(new Mesh(new BoxGeometry(24, 14, 24), new MeshBasicMaterial({ color: new Color(0.6, 0.58, 0.54), side: BackSide })));
  add(11, 7, [0, 6.8, 1], [3.6, 3.5, 3.3]); // overhead softbox
  add(3.4, 8, [-8.5, 2.6, 4.5], [8, 7.7, 7.2]); // key light, camera left
  add(0.9, 8, [8.2, 2.2, 4], [6.4, 6.5, 6.8]); // rim strip, camera right
  add(5.2, 9, [-3.5, 1.6, -9.5], [0.03, 0.03, 0.04]); // black cards behind, which give the dome its dark edges
  add(4.2, 9, [6.6, 1.6, -7.5], [0.05, 0.05, 0.07]);
  add(23, 1.5, [0, -0.1, 11.5], [0.1, 0.1, 0.13]); // a low dark band: the far edge of the counter
  add(6, 4, [10.5, 1.4, -1.5], [0.1, 0.17, 0.5]); // a blue card, the one note of brand colour in the metal
  return room;
}

const RIPPLE_VERTEX = /* glsl */ `
  varying vec2 vPos;
  void main() {
    vPos = position.xy;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const RIPPLE_FRAGMENT = /* glsl */ `
  varying vec2 vPos;
  uniform vec3 uColor;
  uniform float uT[3];
  void main() {
    float d = length(vPos);
    float a = 0.0;
    for (int i = 0; i < 3; i++) {
      float t = uT[i];
      if (t < 0.0 || t > 1.0) continue;
      float eased = 1.0 - pow(1.0 - t, 3.0);
      float r = mix(1.32, 2.1, eased);
      float w = mix(0.012, 0.03, t);
      a += (1.0 - t) * (1.0 - t) * 0.62 * smoothstep(w, 0.0, abs(d - r));
    }
    gl_FragColor = vec4(uColor, a);
  }
`;

const TARGET = new Vector3(0, 0.74, 0);
const REST_AZIMUTH = -0.16;
const REST_ELEVATION = 0.27;
const DISTANCE = 11.1;

export function createBellScene(canvas: HTMLCanvasElement, options: Options): BellScene {
  // The canvas bleeds past its layout box so shadows can fade out. Input stays on the box, away from nearby text.
  const surface = canvas.parentElement!;
  const constrained = window.matchMedia("(pointer: coarse)").matches || navigator.hardwareConcurrency <= 4;
  const segments = constrained ? 88 : 144;

  const renderer = new WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = NeutralToneMapping;
  renderer.toneMappingExposure = 1;

  const scene = new Scene();
  const camera = new PerspectiveCamera(22, 1, 0.1, 60);

  const pmrem = new PMREMGenerator(renderer);
  const room = studioEnvironment();
  const environment = pmrem.fromScene(room, 0.03);
  scene.environment = environment.texture;

  const anisotropy = constrained ? 4 : renderer.capabilities.getMaxAnisotropy();
  const band = bandTexture(options.fontFamily, anisotropy);
  const contact = contactShadowTexture();
  const cast = castShadowTexture();

  const nickel = new MeshPhysicalMaterial({ color: new Color(0.9, 0.885, 0.85), metalness: 1, roughness: 0.14 });
  const chrome = new MeshPhysicalMaterial({ color: new Color(0.93, 0.93, 0.92), metalness: 1, roughness: 0.08 });
  const enamel = new MeshPhysicalMaterial({
    color: new Color(0.012, 0.026, 0.1),
    metalness: 0,
    roughness: 0.46,
    clearcoat: 1,
    clearcoatRoughness: 0.08,
    envMapIntensity: 0.62,
  });
  const lettering = new MeshPhysicalMaterial({
    map: band,
    metalness: 0,
    roughness: 0.46,
    clearcoat: 1,
    clearcoatRoughness: 0.08,
    envMapIntensity: 0.62,
  });

  const lathe = (points: Vector2[], material: MeshPhysicalMaterial) => new Mesh(new LatheGeometry(points, segments), material);

  const bell = new Group();
  bell.add(lathe(bevel([P(0, 0), P(1.24, 0), P(1.24, 0.07), P(1.17, 0.116), P(1.135, 0.125)], 0.02), enamel));
  bell.add(lathe([P(1.135, 0.125), P(1.108, 0.178), P(1.082, 0.232), P(1.055, 0.285)], lettering));
  bell.add(lathe(bevel([P(1.055, 0.285), P(1.04, 0.31), P(0.985, 0.327), P(0, 0.33)], 0.016), enamel));

  const dome = new Group();
  dome.add(lathe(domeProfile(), nickel));
  dome.add(lathe(bevel([P(0.2, 1.195), P(0.19, 1.232), P(0.13, 1.262), P(0.066, 1.268)], 0.012), chrome));
  bell.add(dome);

  const plunger = new Group();
  const stem = new Mesh(new CylinderGeometry(0.052, 0.052, 0.27, 40), chrome);
  stem.position.y = 1.375;
  plunger.add(stem);
  plunger.add(
    lathe(
      bevel([P(0.05, 1.445), P(0.135, 1.456), P(0.205, 1.49), P(0.218, 1.532), P(0.192, 1.578), P(0.12, 1.603), P(0, 1.612)], 0.022),
      chrome,
    ),
  );
  bell.add(plunger);
  scene.add(bell);

  const flat = (size: number, map: CanvasTexture, y: number, x = 0, z = 0) => {
    const m = new Mesh(
      new PlaneGeometry(size, size),
      new MeshBasicMaterial({ map, transparent: true, depthWrite: false, toneMapped: false }),
    );
    m.rotation.x = -Math.PI / 2;
    m.position.set(x, y, z);
    scene.add(m);
    return m;
  };
  flat(4.4, contact, 0.001);
  flat(3.5, cast, 0.0005, 0.62, -0.3);

  const rippleUniforms = { uT: { value: [-1, -1, -1] }, uColor: { value: new Vector3(0.184, 0.357, 0.827) } };
  const rippleMaterial = new ShaderMaterial({
    vertexShader: RIPPLE_VERTEX,
    fragmentShader: RIPPLE_FRAGMENT,
    uniforms: rippleUniforms,
    transparent: true,
    depthWrite: false,
  });
  const ripple = new Mesh(new PlaneGeometry(11, 11), rippleMaterial);
  ripple.rotation.x = -Math.PI / 2;
  ripple.position.y = 0.002;
  scene.add(ripple);

  // ---- State

  let azimuth = REST_AZIMUTH;
  let elevation = REST_ELEVATION;
  let azimuthGoal = azimuth;
  let elevationGoal = elevation;
  let ringStart = -10;
  let introStart: number | null = null;
  let hinted = false;
  let frame = 0;
  let visible = true;
  let disposed = false;
  let lastFrame = 0;

  const wake = () => {
    if (!frame && visible && !disposed) frame = requestAnimationFrame(loop);
  };

  const pressDepth = (t: number) => {
    if (t < 0.06) return -0.095 * (t / 0.06);
    const after = t - 0.06;
    return -0.095 * Math.exp(-after * 9) * Math.cos(after * 14);
  };

  function placeCamera(extra: number) {
    const d = DISTANCE + extra;
    camera.position.set(
      TARGET.x + d * Math.sin(azimuth) * Math.cos(elevation),
      TARGET.y + d * Math.sin(elevation),
      TARGET.z + d * Math.cos(azimuth) * Math.cos(elevation),
    );
    camera.lookAt(TARGET);
  }

  // Returns true while anything is still moving.
  function advance(now: number) {
    let moving = false;
    let intro = 0;
    if (introStart !== null) {
      const p = Math.min(1, (now - introStart) / 1.1);
      intro = Math.pow(1 - p, 3) * 0.9;
      if (p < 1) moving = true;
      else if (!hinted) {
        hinted = true;
        ringStart = now + 0.2;
        moving = true;
      }
    }

    const dt = Math.min(0.05, now - lastFrame);
    lastFrame = now;
    const k = 1 - Math.exp(-dt * 12);
    azimuth += (azimuthGoal - azimuth) * k;
    elevation += (elevationGoal - elevation) * k;
    if (Math.abs(azimuthGoal - azimuth) + Math.abs(elevationGoal - elevation) > 0.0004) moving = true;
    placeCamera(intro);

    const t = now - ringStart;
    if (t < 0) moving = true;
    if (t >= 0 && t < 1.9) {
      moving = true;
      plunger.position.y = pressDepth(t);
      const shiver = 0.0034 * Math.exp(-t * 4.2) * Math.sin(t * Math.PI * 2 * 23);
      dome.scale.set(1 + shiver, 1 - shiver * 0.6, 1 + shiver);
      rippleUniforms.uT.value = [t / 1.5, (t - 0.14) / 1.5, (t - 0.3) / 1.5];
    } else if (t >= 1.9) {
      plunger.position.y = 0;
      dome.scale.set(1, 1, 1);
      rippleUniforms.uT.value = [-1, -1, -1];
    }
    return moving;
  }

  function loop(ms: number) {
    frame = 0;
    if (disposed) return;
    const moving = advance(ms / 1000);
    renderer.render(scene, camera);
    if (moving) wake();
  }

  function resize() {
    const box = canvas.getBoundingClientRect();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, constrained ? 1.5 : 2));
    renderer.setSize(box.width, box.height, false);
    camera.aspect = box.width / box.height;
    camera.updateProjectionMatrix();
    wake();
  }

  // ---- Input

  const ray = new Raycaster();
  let dragging = false;
  let dragged = false;
  let startX = 0;
  let startY = 0;

  const isOnBell = (e: PointerEvent) => {
    const box = canvas.getBoundingClientRect();
    const ndc = new Vector2(((e.clientX - box.left) / box.width) * 2 - 1, -((e.clientY - box.top) / box.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    return ray.intersectObject(bell, true).length > 0;
  };

  const onDown = (e: PointerEvent) => {
    dragging = true;
    dragged = false;
    startX = e.clientX;
    startY = e.clientY;
  };

  const onMove = (e: PointerEvent) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (!dragged && Math.abs(dx) + Math.abs(dy) > 6) {
      dragged = true;
      surface.setPointerCapture(e.pointerId);
    }
    if (!dragged) return;
    azimuthGoal = Math.max(-0.95, Math.min(0.95, azimuthGoal - e.movementX * 0.007));
    if (e.pointerType === "mouse") elevationGoal = Math.max(0.1, Math.min(0.55, elevationGoal + e.movementY * 0.004));
    wake();
  };

  const onUp = (e: PointerEvent) => {
    const wasClick = dragging && !dragged;
    dragging = false;
    if (wasClick && isOnBell(e)) options.onPress();
  };

  const onCancel = () => {
    dragging = false;
  };

  const onLost = (e: Event) => {
    e.preventDefault();
    options.onLost();
  };

  surface.addEventListener("pointerdown", onDown);
  surface.addEventListener("pointermove", onMove);
  surface.addEventListener("pointerup", onUp);
  surface.addEventListener("pointercancel", onCancel);
  canvas.addEventListener("webglcontextlost", onLost);

  const resizer = new ResizeObserver(resize);
  resizer.observe(canvas);

  const onScreen = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    if (visible) wake();
  });
  onScreen.observe(canvas);

  // ---- First frame

  resize();
  lastFrame = performance.now() / 1000;
  placeCamera(0.9);
  renderer.render(scene, camera);
  options.onReady();
  introStart = performance.now() / 1000;
  wake();

  return {
    ring() {
      ringStart = performance.now() / 1000;
      wake();
    },
    dispose() {
      disposed = true;
      cancelAnimationFrame(frame);
      resizer.disconnect();
      onScreen.disconnect();
      surface.removeEventListener("pointerdown", onDown);
      surface.removeEventListener("pointermove", onMove);
      surface.removeEventListener("pointerup", onUp);
      surface.removeEventListener("pointercancel", onCancel);
      canvas.removeEventListener("webglcontextlost", onLost);
      for (const root of [scene, room]) {
        root.traverse((o) => {
          const m = o as Mesh;
          m.geometry?.dispose();
          (m.material as MeshBasicMaterial | undefined)?.dispose?.();
        });
      }
      band.dispose();
      contact.dispose();
      cast.dispose();
      environment.dispose();
      pmrem.dispose();
      renderer.dispose();
    },
  };
}
