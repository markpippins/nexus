import { RoleSpec, HierarchySetting, LayoutBias, DensitySetting } from "../types/designIR";

export type Region = "main" | "sidebar" | "header" | "footer" | "overlay";

export interface RegionResolverContext {
  hierarchy: HierarchySetting;
  density?: DensitySetting;
}

export function resolveRegion(
  roleName: string,
  role: RoleSpec,
  ctx: RegionResolverContext,
): Region {
  const { hierarchy } = ctx;

  if (role.constraints?.layoutBias) {
    return mapLayoutBiasToRegion(role.constraints.layoutBias);
  }

  if (hierarchy.primaryRoles?.includes(roleName)) {
    return "main";
  }

  if (hierarchy.secondaryRoles?.includes(roleName)) {
    return "sidebar";
  }

  if (hierarchy.ambientRoles?.includes(roleName)) {
    return "footer";
  }

  if (role.priority === "primary") {
    return "main";
  }

  if (role.priority === "secondary") {
    return "sidebar";
  }

  if (role.priority === "ambient") {
    return "footer";
  }

  return "main";
}

function mapLayoutBiasToRegion(bias: LayoutBias): Region {
  const map: Record<LayoutBias, Region> = {
    main: "main",
    sidebar: "sidebar",
    header: "header",
    footer: "footer",
    overlay: "overlay",
  };
  return map[bias] || "main";
}

export function getRegionPriority(region: Region, density?: DensitySetting): number {
  const basePriority: Record<Region, number> = {
    main: 0,
    header: 1,
    sidebar: 2,
    footer: 3,
    overlay: 4,
  };

  let priority = basePriority[region] || 0;

  if (density === "highSalience" && region === "main") {
    priority -= 0.5;
  }

  if (density === "compact" && region === "footer") {
    priority += 0.5;
  }

  return priority;
}

export interface ResolvedRegion {
  region: Region;
  order: number;
  priority: number;
}

export function resolveRegionWithOrder(
  roleName: string,
  role: RoleSpec,
  ctx: RegionResolverContext,
  allRoles: Array<{ name: string; spec: RoleSpec }>,
): ResolvedRegion {
  const region = resolveRegion(roleName, role, ctx);

  const basePriority = role.priority === "primary" ? 0 : role.priority === "secondary" ? 1 : 2;

  const densityOffset =
    role.density === "highSalience" ? -0.5 : role.density === "compact" ? 0.5 : 0;

  const priority = basePriority + densityOffset;

  const sameRegionRoles = allRoles
    .filter((r) => resolveRegion(r.name, r.spec, ctx) === region)
    .sort((a, b) => {
      const aPriority = a.spec.priority === "primary" ? 0 : a.spec.priority === "secondary" ? 1 : 2;
      const bPriority = b.spec.priority === "primary" ? 0 : b.spec.priority === "secondary" ? 1 : 2;
      return aPriority - bPriority;
    });

  const order = sameRegionRoles.findIndex((r) => r.name === roleName);

  return { region, order: order >= 0 ? order : 0, priority };
}
