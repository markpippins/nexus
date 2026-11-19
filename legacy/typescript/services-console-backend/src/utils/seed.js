const { PrismaClient } = require('@prisma/client');
const prisma = new PrismaClient();

async function main() {
  console.log('🌱 Starting database seeding...');

  // Clean existing data
  console.log('🧹 Cleaning existing data...');
  await prisma.deployment.deleteMany();
  await prisma.serviceDependency.deleteMany();
  await prisma.serviceConfig.deleteMany();
  await prisma.service.deleteMany();
  await prisma.server.deleteMany();
  await prisma.framework.deleteMany();
  await prisma.operatingSystem.deleteMany();
  await prisma.language.deleteMany();
  await prisma.vendor.deleteMany();
  await prisma.category.deleteMany();
  await prisma.environmentType.deleteMany();
  await prisma.serverType.deleteMany();
  await prisma.serviceType.deleteMany();
  await prisma.configType.deleteMany();

  // Seed Vendors
  console.log('📦 Seeding vendors...');
  const vendors = await Promise.all([
    prisma.vendor.create({
      data: { id: 'v_google', name: 'Google', url: 'https://google.com' }
    }),
    prisma.vendor.create({
      data: { id: 'v_ms', name: 'Microsoft', url: 'https://microsoft.com' }
    }),
    prisma.vendor.create({
      data: { id: 'v_fb', name: 'Meta', url: 'https://meta.com' }
    }),
    prisma.vendor.create({
      data: { id: 'v_aws', name: 'Amazon', url: 'https://aws.amazon.com' }
    }),
    prisma.vendor.create({
      data: { id: 'v_canonical', name: 'Canonical', url: 'https://canonical.com' }
    })
  ]);

  // Seed Categories
  console.log('📂 Seeding categories...');
  const categories = await Promise.all([
    prisma.category.create({
      data: { id: 'c_fe', name: 'Frontend' }
    }),
    prisma.category.create({
      data: { id: 'c_be', name: 'Backend' }
    }),
    prisma.category.create({
      data: { id: 'c_infra', name: 'Infrastructure' }
    })
  ]);

  // Seed Languages
  console.log('💻 Seeding languages...');
  const languages = await Promise.all([
    prisma.language.create({
      data: {
        id: 'l_ts',
        name: 'TypeScript',
        description: 'Typed superset of JavaScript.',
        url: 'https://typescriptlang.org',
        current_version: '5.3',
        lts_version: '5.0'
      }
    }),
    prisma.language.create({
      data: {
        id: 'l_go',
        name: 'Go',
        description: 'Statically typed, compiled language.',
        url: 'https://go.dev',
        current_version: '1.21',
        lts_version: '1.20'
      }
    }),
    prisma.language.create({
      data: {
        id: 'l_py',
        name: 'Python',
        description: 'Interpreted, high-level.',
        url: 'https://python.org',
        current_version: '3.12',
        lts_version: '3.10'
      }
    })
  ]);

  // Seed Frameworks
  console.log('🏗️ Seeding frameworks...');
  const frameworks = await Promise.all([
    prisma.framework.create({
      data: {
        id: 'f_ang',
        vendor_id: 'v_google',
        name: 'Angular',
        description: 'Platform for building mobile and desktop web applications.',
        category_id: 'c_fe',
        language_id: 'l_ts',
        current_version: '17.0',
        lts_version: '16.0',
        url: 'https://angular.io'
      }
    }),
    prisma.framework.create({
      data: {
        id: 'f_react',
        vendor_id: 'v_fb',
        name: 'React',
        description: 'Library for web and native user interfaces.',
        category_id: 'c_fe',
        language_id: 'l_ts',
        current_version: '18.2',
        lts_version: '18.0',
        url: 'https://react.dev'
      }
    })
  ]);

  // Seed Operating Systems
  console.log('🖥️ Seeding operating systems...');
  const operatingSystems = await Promise.all([
    prisma.operatingSystem.create({
      data: {
        id: 'os_ubu',
        vendor_id: 'v_canonical',
        name: 'Ubuntu Server',
        description: 'Popular Linux distribution.',
        current_version: '23.10',
        lts_version: '22.04'
      }
    }),
    prisma.operatingSystem.create({
      data: {
        id: 'os_win',
        vendor_id: 'v_ms',
        name: 'Windows Server',
        description: 'Server OS by Microsoft.',
        current_version: '2022',
        lts_version: '2019'
      }
    })
  ]);

  // Seed Environment Types
  console.log('🌍 Seeding environment types...');
  const environmentTypes = await Promise.all([
    prisma.environmentType.create({
      data: { id: 'e_prod', name: 'Production' }
    }),
    prisma.environmentType.create({
      data: { id: 'e_stage', name: 'Staging' }
    }),
    prisma.environmentType.create({
      data: { id: 'e_dev', name: 'Development' }
    })
  ]);

  // Seed Server Types
  console.log('🖴 Seeding server types...');
  const serverTypes = await Promise.all([
    prisma.serverType.create({
      data: { id: 'st_vm', name: 'Virtual Machine' }
    }),
    prisma.serverType.create({
      data: { id: 'st_phy', name: 'Physical Blade' }
    }),
    prisma.serverType.create({
      data: { id: 'st_cont', name: 'Container Node' }
    })
  ]);

  // Seed Servers
  console.log('🖥️ Seeding servers...');
  const servers = await Promise.all([
    prisma.server.create({
      data: {
        id: 'srv_01',
        hostname: 'prod-api-01',
        ip_address: '10.0.1.5',
        server_type_id: 'st_vm',
        environment_type_id: 'e_prod',
        operating_system_id: 'os_ubu',
        cpu_cores: 8,
        memory: '16GB',
        disk: '500GB SSD',
        status: 'Online',
        description: 'Primary API Gateway Node'
      }
    }),
    prisma.server.create({
      data: {
        id: 'srv_02',
        hostname: 'db-primary',
        ip_address: '10.0.1.20',
        server_type_id: 'st_phy',
        environment_type_id: 'e_prod',
        operating_system_id: 'os_ubu',
        cpu_cores: 32,
        memory: '128GB',
        disk: '4TB NVMe',
        status: 'Online',
        description: 'Master Database'
      }
    }),
    prisma.server.create({
      data: {
        id: 'srv_03',
        hostname: 'dev-sandbox',
        ip_address: '10.0.2.10',
        server_type_id: 'st_vm',
        environment_type_id: 'e_dev',
        operating_system_id: 'os_win',
        cpu_cores: 4,
        memory: '8GB',
        disk: '100GB',
        status: 'Offline',
        description: 'Dev Playground',
        active_flag: false
      }
    })
  ]);

  // Seed Service Types
  console.log('🔧 Seeding service types...');
  const serviceTypes = await Promise.all([
    prisma.serviceType.create({
      data: { id: 'svt_micro', name: 'Microservice' }
    }),
    prisma.serviceType.create({
      data: { id: 'svt_mono', name: 'Monolith' }
    }),
    prisma.serviceType.create({
      data: { id: 'svt_batch', name: 'Batch Job' }
    })
  ]);

  // Seed Services
  console.log('⚡ Seeding services...');
  const services = await Promise.all([
    prisma.service.create({
      data: {
        id: 'svc_auth',
        name: 'Auth Service',
        description: 'Handles user authentication and token generation.',
        framework_id: 'f_ang',
        service_type_id: 'svt_micro',
        default_port: 3000,
        api_base_path: '/api/v1/auth',
        repository_url: 'git://repo/auth',
        version: '2.1.0',
        status: 'Active'
      }
    }),
    prisma.service.create({
      data: {
        id: 'svc_pay',
        name: 'Payment Gateway',
        description: 'Processes credit card transactions.',
        framework_id: 'f_react',
        service_type_id: 'svt_micro',
        default_port: 8080,
        api_base_path: '/api/v1/payments',
        repository_url: 'git://repo/pay',
        version: '1.0.5',
        status: 'Active'
      }
    })
  ]);

  // Seed Config Types
  console.log('⚙️ Seeding config types...');
  const configTypes = await Promise.all([
    prisma.configType.create({
      data: { id: 'ct_db', name: 'Database' }
    }),
    prisma.configType.create({
      data: { id: 'ct_api', name: 'API Key' }
    }),
    prisma.configType.create({
      data: { id: 'ct_flag', name: 'Feature Flag' }
    })
  ]);

  // Seed Service Configs
  console.log('🔧 Seeding service configs...');
  const serviceConfigs = await Promise.all([
    prisma.serviceConfig.create({
      data: {
        id: 'sc_1',
        service_id: 'svc_pay',
        config_type_id: 'ct_api',
        description: 'Stripe API Key',
        config_key: 'STRIPE_KEY',
        config_value: 'sk_test_123456',
        environment_id: 'e_dev'
      }
    }),
    prisma.serviceConfig.create({
      data: {
        id: 'sc_2',
        service_id: 'svc_auth',
        config_type_id: 'ct_db',
        description: 'DB Connection String',
        config_key: 'DB_URL',
        config_value: 'postgres://user:pass@localhost:5432/db',
        environment_id: 'e_dev'
      }
    })
  ]);

  // Seed Service Dependencies
  console.log('🔗 Seeding service dependencies...');
  const serviceDependencies = await Promise.all([
    prisma.serviceDependency.create({
      data: {
        id: 'sd_1',
        service_id: 'svc_pay',
        target_service_id: 'svc_auth',
        criticality: 'High',
        description: 'Payment needs Auth to validate tokens'
      }
    })
  ]);

  // Seed Deployments
  console.log('🚀 Seeding deployments...');
  const deployments = await Promise.all([
    prisma.deployment.create({
      data: {
        id: 'dep_1',
        service_id: 'svc_auth',
        environment_id: 'e_prod',
        server_id: 'srv_01',
        version: '2.1.0',
        deployed_at: new Date('2023-11-15T14:30:00Z'),
        status: 'Success'
      }
    }),
    prisma.deployment.create({
      data: {
        id: 'dep_2',
        service_id: 'svc_pay',
        environment_id: 'e_dev',
        server_id: 'srv_03',
        version: '1.0.5',
        deployed_at: new Date('2023-11-16T09:15:00Z'),
        status: 'Success'
      }
    })
  ]);

  console.log('✅ Database seeding completed successfully!');
  console.log(`
📊 Summary:
- Vendors: ${vendors.length}
- Categories: ${categories.length}
- Languages: ${languages.length}
- Frameworks: ${frameworks.length}
- Operating Systems: ${operatingSystems.length}
- Environment Types: ${environmentTypes.length}
- Server Types: ${serverTypes.length}
- Servers: ${servers.length}
- Service Types: ${serviceTypes.length}
- Services: ${services.length}
- Config Types: ${configTypes.length}
- Service Configs: ${serviceConfigs.length}
- Service Dependencies: ${serviceDependencies.length}
- Deployments: ${deployments.length}
  `);
}

main()
  .catch((e) => {
    console.error('❌ Error during seeding:', e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });