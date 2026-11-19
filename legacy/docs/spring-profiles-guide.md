# Spring Boot Profiles Configuration Guide

## Overview

Spring Boot profiles allow you to maintain different configurations for different environments without changing code or doing search-and-replace on IP addresses.

## Available Profiles

### 1. `selenium` (Default)
- **Use case:** Running broker-gateway on Selenium machine
- **File system server:** Points to Beryllium (172.16.30.57:4040)
- **MongoDB:** localhost on Selenium
- **Config file:** `application-selenium.properties`

### 2. `beryllium`
- **Use case:** Running broker-gateway on Beryllium machine
- **File system server:** localhost (Beryllium's own FS server)
- **MongoDB:** localhost on Beryllium
- **Config file:** `application-beryllium.properties`

### 3. `dev`
- **Use case:** Local development/debugging
- **File system server:** localhost
- **MongoDB:** localhost
- **Extra logging:** DEBUG level enabled
- **Config file:** `application-dev.properties`

## How to Use Profiles

### Method 1: Command Line Argument (Recommended)

When starting the Spring Boot application:

```bash
# Run with selenium profile
java -jar broker-gateway.jar --spring.profiles.active=selenium

# Run with beryllium profile
java -jar broker-gateway.jar --spring.profiles.active=beryllium

# Run with dev profile
java -jar broker-gateway.jar --spring.profiles.active=dev
```

### Method 2: Environment Variable

Set the environment variable before starting the application:

```bash
# Linux/Mac
export SPRING_PROFILES_ACTIVE=beryllium
java -jar broker-gateway.jar

# Windows
set SPRING_PROFILES_ACTIVE=beryllium
java -jar broker-gateway.jar
```

### Method 3: IDE Configuration

#### IntelliJ IDEA
1. Edit Run Configuration
2. Add to "VM options": `-Dspring.profiles.active=beryllium`
3. Or add to "Program arguments": `--spring.profiles.active=beryllium`

#### Eclipse
1. Run → Run Configurations
2. Select your application
3. Arguments tab → Program arguments: `--spring.profiles.active=beryllium`

#### VS Code
In `launch.json`:
```json
{
  "type": "java",
  "name": "BrokerGatewayApplication (beryllium)",
  "request": "launch",
  "mainClass": "com.angrysurfer.atomic.broker.BrokerGatewayApplication",
  "args": "--spring.profiles.active=beryllium"
}
```

### Method 4: Maven

```bash
# Run with Maven
mvn spring-boot:run -Dspring-boot.run.profiles=beryllium

# Package and run
mvn clean package -DskipTests
java -jar target/broker-gateway-1.0.0-SNAPSHOT.jar --spring.profiles.active=beryllium
```

## Configuration Files Structure

```
spring/broker-gateway/src/main/resources/
├── application.properties              # Common config for all profiles
├── application-selenium.properties     # Selenium-specific config
├── application-beryllium.properties    # Beryllium-specific config
└── application-dev.properties          # Development-specific config
```

## How It Works

1. Spring Boot loads `application.properties` first (common config)
2. Then loads `application-{profile}.properties` based on active profile
3. Profile-specific properties override common properties
4. You can activate multiple profiles: `--spring.profiles.active=beryllium,debug`

## Verifying Active Profile

When the application starts, check the logs:

```
The following 1 profile is active: "beryllium"
```

Or add this to any controller to check at runtime:

```java
@Value("${spring.profiles.active:default}")
private String activeProfile;
```

## Creating New Profiles

To add a new profile (e.g., for production):

1. Create `application-production.properties`:
```properties
restfs.api.url=http://production-fs-server:4040/fs
spring.data.mongodb.uri=mongodb://user:pass@prod-mongo:27017/broker-gateway?authSource=admin
server.port=8080
```

2. Activate it:
```bash
java -jar broker-gateway.jar --spring.profiles.active=production
```

## Profile-Specific Configuration Examples

### Different Ports per Environment

```properties
# application-dev.properties
server.port=8080

# application-selenium.properties
server.port=8080

# application-beryllium.properties
server.port=8080
```

### Different Log Levels

```properties
# application-dev.properties
logging.level.root=DEBUG
logging.level.com.angrysurfer.atomic=TRACE

# application-selenium.properties
logging.level.root=INFO
logging.level.com.angrysurfer.atomic=INFO

# application-beryllium.properties
logging.level.root=INFO
logging.level.com.angrysurfer.atomic=DEBUG
```

### Different Database Connections

```properties
# application-dev.properties
spring.data.mongodb.uri=mongodb://localhost:27017/broker-gateway-dev

# application-selenium.properties
spring.data.mongodb.uri=mongodb://mongoUser:somePassword@localhost:27017/broker-gateway?authSource=admin

# application-beryllium.properties
spring.data.mongodb.uri=mongodb://mongoUser:somePassword@localhost:27017/broker-gateway?authSource=admin
```

## Environment-Specific Startup Scripts

### For Selenium

Create `start-selenium.sh`:
```bash
#!/bin/bash
cd /path/to/spring/broker-gateway
java -jar target/broker-gateway-1.0.0-SNAPSHOT.jar --spring.profiles.active=selenium
```

### For Beryllium

Create `start-beryllium.sh`:
```bash
#!/bin/bash
cd /path/to/spring/broker-gateway
java -jar target/broker-gateway-1.0.0-SNAPSHOT.jar --spring.profiles.active=beryllium
```

Make them executable:
```bash
chmod +x start-selenium.sh start-beryllium.sh
```

## Systemd Service with Profiles

Create `/etc/systemd/system/broker-gateway.service`:

```ini
[Unit]
Description=Broker Gateway Service
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/spring/broker-gateway
ExecStart=/usr/bin/java -jar target/broker-gateway-1.0.0-SNAPSHOT.jar --spring.profiles.active=beryllium
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable broker-gateway
sudo systemctl start broker-gateway
```

## Docker with Profiles

In `Dockerfile`:
```dockerfile
FROM openjdk:21-jdk-slim
COPY target/broker-gateway-1.0.0-SNAPSHOT.jar app.jar
ENTRYPOINT ["java", "-jar", "/app.jar"]
CMD ["--spring.profiles.active=beryllium"]
```

Or override at runtime:
```bash
docker run -e SPRING_PROFILES_ACTIVE=beryllium broker-gateway
```

## Troubleshooting

### Profile Not Loading

**Check:**
1. File name is exactly `application-{profile}.properties`
2. File is in `src/main/resources/`
3. Profile name matches exactly (case-sensitive)
4. Check startup logs for "The following profiles are active"

### Wrong Configuration Being Used

**Check:**
1. Verify active profile in logs
2. Check if property is in common `application.properties` (it will be overridden)
3. Use `--debug` flag to see property sources

### Multiple Profiles

You can activate multiple profiles:
```bash
java -jar app.jar --spring.profiles.active=beryllium,debug,custom
```

Properties are loaded in order, later profiles override earlier ones.

## Best Practices

1. **Keep common config in `application.properties`**
   - Logging patterns
   - Application name
   - Common dependencies

2. **Use profiles for environment-specific values**
   - URLs
   - Database connections
   - API keys
   - Feature flags

3. **Don't commit sensitive data**
   - Use environment variables for passwords
   - Or use Spring Cloud Config
   - Or use encrypted properties with Jasypt

4. **Document your profiles**
   - Keep this guide updated
   - Add comments in property files
   - Document in README

5. **Test each profile**
   - Verify startup with each profile
   - Check health endpoints
   - Validate connections

## Quick Reference

| Profile | Use Case | FS Server | MongoDB | Logging |
|---------|----------|-----------|---------|---------|
| selenium | Selenium machine | 172.16.30.57:4040 | localhost | INFO |
| beryllium | Beryllium machine | localhost:4040 | localhost | INFO |
| dev | Local development | localhost:4040 | localhost | DEBUG |

## Current Configuration Summary

### Selenium Profile (Default)
```properties
restfs.api.url=http://172.16.30.57:4040/fs
spring.data.mongodb.uri=mongodb://mongoUser:somePassword@localhost:27017/broker-gateway?authSource=admin
server.port=8080
```

### Beryllium Profile
```properties
restfs.api.url=http://localhost:4040/fs
spring.data.mongodb.uri=mongodb://mongoUser:somePassword@localhost:27017/broker-gateway?authSource=admin
server.port=8080
```

### Dev Profile
```properties
restfs.api.url=http://localhost:4040/fs
spring.data.mongodb.uri=mongodb://mongoUser:somePassword@localhost:27017/broker-gateway?authSource=admin
server.port=8080
logging.level.com.angrysurfer.atomic=DEBUG
```
